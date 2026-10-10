#!/usr/bin/env python3
"""The portable bootstrap syncs code and writes secrets without echoing them."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "init.py"
spec = importlib.util.spec_from_file_location("pilot_init", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class InitTests(unittest.TestCase):
    def test_sync_tree_updates_code_and_removes_stale_entries(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            source, target = root / "source", root / "target"
            (source / "plugin").mkdir(parents=True)
            (source / "plugin" / "name.py").write_text("new", encoding="utf-8")
            (target / "stale").mkdir(parents=True)
            (target / "stale" / "old.py").write_text("old", encoding="utf-8")
            module.sync_tree(source, target)
            self.assertEqual((target / "plugin" / "name.py").read_text(), "new")
            self.assertFalse((target / "stale").exists())

    def test_private_env_write_replaces_existing_values(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / ".env"
            module.write_private(path, "API_SERVER_KEY=first\n")
            module.write_private(path, module.replace_keys(path.read_text(),
                                                          {"API_SERVER_KEY": "second"}))
            self.assertEqual(path.read_text(), "API_SERVER_KEY=second\n")
            if module.os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
