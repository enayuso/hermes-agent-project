"""Profile cutover keeps a complete rollback point."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

import yaml


POC = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "apply_boundary_config", POC / "scripts" / "apply-boundary-config.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
policy = yaml.safe_load((POC / "config" / "boundary-tool-policy.yaml").read_text())


class ApplyBoundaryConfigTests(unittest.TestCase):
    def test_apply_and_restore_every_profile_without_losing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            personal = data / "profiles" / "person-example"
            personal.mkdir(parents=True)
            original = b"terminal:\n  backend: local\ncustom: keep-me\n"
            for home in (data, personal):
                (home / "config.yaml").write_bytes(original)
            homes = module.apply(data, policy)
            self.assertEqual(homes, [data, personal])
            for home in homes:
                changed = yaml.safe_load((home / "config.yaml").read_text())
                self.assertEqual(changed["custom"], "keep-me")
                self.assertEqual(changed["terminal"]["backend"], "pilot_sandbox")
                self.assertEqual(changed["terminal"]["cwd"], "/workspace")
                self.assertFalse(changed["terminal"]["container_persistent"])
                self.assertEqual(module.backup_path(home).read_bytes(), original)
            with self.assertRaises(FileExistsError):
                module.apply(data, policy)
            module.restore(data)
            for home in homes:
                self.assertEqual((home / "config.yaml").read_bytes(), original)
                self.assertFalse(module.backup_path(home).exists())


if __name__ == "__main__":
    unittest.main()
