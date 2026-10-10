"""The pilot LaunchAgent must restart only the fixed host broker program."""

import importlib.util
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "render-macos-broker-agent.py"
spec = importlib.util.spec_from_file_location("pilot_launch_agent", SOURCE)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
STAGER = SOURCE.with_name("stage-macos-broker-runtime.py")
stage_spec = importlib.util.spec_from_file_location("pilot_runtime_stage", STAGER)
stager = importlib.util.module_from_spec(stage_spec)
stage_spec.loader.exec_module(stager)


class LaunchAgentTests(unittest.TestCase):
    def test_definition_uses_fixed_program_and_policy_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            poc = Path(directory)
            (poc / "scripts").mkdir()
            (poc / "config").mkdir()
            (poc / "scripts" / "start-sandbox-broker.py").touch()
            (poc / "config" / "egress-policy.json").touch()
            spec_dict = renderer.definition(poc=poc, python=Path(sys.executable))
            loaded = plistlib.loads(plistlib.dumps(spec_dict))
            self.assertEqual(loaded["ProgramArguments"][1],
                             str((poc / "scripts" / "start-sandbox-broker.py").resolve()))
            self.assertEqual(loaded["EnvironmentVariables"]["PILOT_EGRESS_POLICY_FILE"],
                             str((poc / "config" / "egress-policy.json").resolve()))
            self.assertTrue(loaded["RunAtLoad"])
            self.assertTrue(loaded["KeepAlive"])
            self.assertNotIn("TOKEN", str(loaded).upper())

    def test_stages_only_broker_files_with_private_token(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            runtime = Path(directory) / "runtime"
            for relative in stager.FILES:
                item = source / relative
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text("t" * 32 if relative == ".sandbox-broker-token" else relative)
            (source / ".env").write_text("must-not-copy")
            stager.stage(source, runtime)
            self.assertEqual((runtime / ".sandbox-broker-token").read_text(), "t" * 32)
            self.assertEqual((runtime / ".sandbox-broker-token").stat().st_mode & 0o777, 0o600)
            self.assertFalse((runtime / ".env").exists())
            self.assertEqual(len([p for p in runtime.rglob("*") if p.is_file()]),
                             len(stager.FILES))


if __name__ == "__main__":
    unittest.main()
