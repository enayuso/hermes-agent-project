"""The pilot launcher keeps Docker authority and secret paths out of workers."""

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "pilot-boundary.py"
spec = importlib.util.spec_from_file_location("pilot_boundary", SOURCE)
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class PilotBoundaryTests(unittest.TestCase):
    def test_gateway_mounts_data_and_token_but_no_docker_socket(self):
        command = pilot.pilot_command()
        self.assertEqual(command[:3], ["run", "-d", "--name"])
        self.assertIn("HERMES_WRITE_SAFE_ROOT=/workspace", command)
        self.assertIn("PILOT_SANDBOX_TOKEN_FILE=/run/pilot-broker-token", command)
        mounts = [command[i + 1] for i, item in enumerate(command[:-1]) if item == "--mount"]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(any("target=/run/pilot-broker-token,readonly" in m for m in mounts))
        self.assertFalse(any("docker.sock" in m for m in mounts))
        self.assertEqual(command[-2:], ["/opt/hermes/.venv/bin/python",
                                        "/opt/data/operator/run-gateway-with-boundary.py"])

    def test_failed_new_container_restores_old_container_and_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            poc = Path(directory)
            data = poc / "data"
            data.mkdir()
            (poc / "config").mkdir()
            (poc / ".env").write_text("HERMES_UID=10000\n")
            source_policy = SOURCE.parents[1] / "config" / "boundary-tool-policy.yaml"
            shutil.copy2(source_policy, poc / "config" / "boundary-tool-policy.yaml")
            original = b"terminal:\n  backend: local\n"
            (data / "config.yaml").write_bytes(original)
            state = {pilot.NEW: True, pilot.OLD: False}

            def exists(name):
                return state[name]

            def fake_docker(*args, **kwargs):
                if args[0] == "run":
                    raise RuntimeError("simulated new container failure")
                if args[0] == "rename":
                    state[args[1]], state[args[2]] = False, True
                elif args[0] == "rm":
                    state[args[-1]] = False
                elif args[0] == "start":
                    self.assertTrue(state[args[1]])

            with patch.object(pilot, "POC", poc), patch.object(pilot, "DATA", data), \
                 patch.object(pilot, "ensure_broker"), \
                 patch.object(pilot, "container_exists", side_effect=exists), \
                 patch.object(pilot, "docker", side_effect=fake_docker), \
                 patch.object(pilot, "wait_health", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    pilot.apply()
            self.assertEqual(state, {pilot.NEW: True, pilot.OLD: False})
            self.assertEqual((data / "config.yaml").read_bytes(), original)
            self.assertFalse((data / "config.yaml.before-boundary").exists())


if __name__ == "__main__":
    unittest.main()
