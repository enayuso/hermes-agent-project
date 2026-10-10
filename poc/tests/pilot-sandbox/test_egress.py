"""Destination and DNS checks for credential-free broker egress."""

import importlib.util
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "sandbox" / "egress.py"
spec = importlib.util.spec_from_file_location("pilot_egress", SOURCE)
egress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(egress)


class EgressTests(unittest.TestCase):
    def test_no_policy_denies_all(self):
        self.assertEqual(egress.load_policy(None), frozenset())
        with self.assertRaises(PermissionError):
            egress.validate_url("https://example.org/", frozenset())

    def test_exact_host_and_https_only(self):
        hosts = frozenset({"example.org"})
        self.assertEqual(egress.validate_url("https://example.org/a?q=1", hosts),
                         ("example.org", "/a?q=1"))
        self.assertEqual(egress.validate_url("https://example.org/한국어?q=français", hosts),
                         ("example.org", "/%ED%95%9C%EA%B5%AD%EC%96%B4?q=fran%C3%A7ais"))
        for url in ("http://example.org/", "https://a.example.org/",
                    "https://user@example.org/", "https://example.org:8443/",
                    "https://example.org\\@other.test/", "https://127.0.0.1/"):
            with self.assertRaises((ValueError, PermissionError), msg=url):
                egress.validate_url(url, hosts)

    def test_policy_rejects_wildcards_and_ip_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for host in ("*.example.org", "127.0.0.1", "EXAMPLE.ORG"):
                path.write_text(json.dumps({"version": 1, "hosts": [host]}))
                with self.assertRaises(ValueError):
                    egress.load_policy(path)

    def test_mixed_public_private_dns_fails_closed(self):
        records = [(socket.AF_INET, 1, 6, "", ("8.8.8.8", 443)),
                   (socket.AF_INET, 1, 6, "", ("169.254.169.254", 443))]
        with patch.object(egress.socket, "getaddrinfo", return_value=records):
            with self.assertRaises(PermissionError):
                egress.public_addresses("example.org")

    def test_tls_connects_to_checked_ip_and_verifies_named_host(self):
        with patch.object(egress.socket, "create_connection") as connect, \
             patch.object(egress.ssl, "create_default_context") as context:
            connection = egress._PinnedHTTPS("example.org", "8.8.8.8")
            connection.connect()
            connect.assert_called_once_with(("8.8.8.8", 443), timeout=12)
            context.return_value.wrap_socket.assert_called_once_with(
                connect.return_value, server_hostname="example.org")

    def test_redirect_does_not_follow(self):
        class Response:
            status = 302

        class Connection:
            def __init__(self, host, address):
                self.host = host
                self.address = address
                self.requested = None

            def request(self, method, target, headers):
                self.requested = (method, target, headers)

            def getresponse(self):
                return Response()

            def close(self):
                pass

        connection = Connection("example.org", "8.8.8.8")
        with patch.object(egress, "public_addresses", return_value=["8.8.8.8"]), \
             patch.object(egress, "_PinnedHTTPS", return_value=connection):
            with self.assertRaises(PermissionError):
                egress.PublicEgress(frozenset({"example.org"})).fetch(
                    "https://example.org/start")
        self.assertEqual(connection.requested[0:2], ("GET", "/start"))
        self.assertNotIn("Authorization", connection.requested[2])

    def test_slow_response_closes_connection(self):
        class Response:
            status = 200

            def getheader(self, name, default=""):
                return "text/plain" if name == "Content-Type" else default

            def read1(self, size):
                return b"x"

        class Connection:
            closed = False

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        connection = Connection()
        with patch.object(egress, "public_addresses", return_value=["8.8.8.8"]), \
             patch.object(egress, "_PinnedHTTPS", return_value=connection), \
             patch.object(egress.time, "monotonic", side_effect=[0, 1, 21]):
            with self.assertRaises(TimeoutError):
                egress.PublicEgress(frozenset({"example.org"})).fetch(
                    "https://example.org/start")
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
