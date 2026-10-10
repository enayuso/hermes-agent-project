"""The fixed search route cannot change providers or leak a credential."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))
import search


class SearchTests(unittest.TestCase):
    def test_requires_exact_provider_host(self):
        with self.assertRaises(PermissionError):
            search.PublicSearch(frozenset({"github.com"})).search("Pilot Agent")
        for query, limit in (("", 5), ("x" * 1001, 5), ("test", 0),
                             ("test", 11), ("test", True)):
            with self.assertRaises(ValueError):
                search.validate_search(query, limit)

    def test_uses_fixed_mcp_endpoint_and_returns_bounded_results(self):
        rows = {"results": [{"url": "https://github.com/x", "title": "한글",
                             "excerpts": ["résumé"]}] * 3}
        envelope = {"result": {"content": [{"type": "text",
                                                "text": json.dumps(rows)}]}}

        class Response:
            status = 200

            def __init__(self):
                self.data = json.dumps(envelope).encode("utf-8")

            def getheader(self, name, default=""):
                return default

            def read1(self, size):
                data, self.data = self.data[:size], self.data[size:]
                return data

        class Connection:
            def __init__(self):
                self.call = None
                self.closed = False

            def request(self, *args, **kwargs):
                self.call = args, kwargs

            def getresponse(self):
                return Response()

            def close(self):
                self.closed = True

        connection = Connection()
        with patch.object(search, "public_addresses", return_value=["8.8.8.8"]), \
             patch.object(search, "_PinnedHTTPS", return_value=connection) as pinned:
            result = search.PublicSearch(frozenset({search.SEARCH_HOST})).search("한국어", 2)
        pinned.assert_called_once_with(search.SEARCH_HOST, "8.8.8.8")
        args, kwargs = connection.call
        self.assertEqual(args, ("POST", "/mcp"))
        self.assertEqual(kwargs["headers"]["Host"], search.SEARCH_HOST)
        self.assertNotIn("Authorization", kwargs["headers"])
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["params"]["name"], "web_search")
        self.assertEqual(payload["params"]["arguments"]["search_queries"], ["한국어"])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual(result["results"][0]["title"], "한글")
        self.assertTrue(connection.closed)

    def test_sse_parse_keeps_unicode_line_separators_in_text(self):
        text = "한\u2028글"
        body = "event: message\ndata: " + json.dumps({"result": {
            "content": [{"text": text}] }}, ensure_ascii=False) + "\n\n"
        self.assertEqual(search._mcp_text(body), text)


if __name__ == "__main__":
    unittest.main()
