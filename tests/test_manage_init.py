"""Unit tests for manage.py init command."""

from pathlib import Path
import shutil
import tempfile
import unittest

import manage


class TestManageInit(unittest.TestCase):
    def test_init_command_creates_structure_and_token(self):
        with tempfile.TemporaryDirectory() as td:
            orig_root = manage.ROOT
            try:
                manage.ROOT = Path(td)
                # Run init
                manage.init_project()

                # Verify essential directories exist
                self.assertTrue((manage.ROOT / "dist").is_dir())
                self.assertTrue((manage.ROOT / "profiles").is_dir())
                self.assertTrue((manage.ROOT / ".state").is_dir())
                self.assertTrue((manage.ROOT / "local").is_dir())
                self.assertTrue((manage.ROOT / "subscriptions").is_dir())
                self.assertTrue((manage.ROOT / "rules").is_dir())
            finally:
                manage.ROOT = orig_root


if __name__ == "__main__":
    unittest.main()
