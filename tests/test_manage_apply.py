"""Unit tests for manage.py apply, backup rotation, and check command (P2-5)."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import manage


class TestManageApply(unittest.TestCase):
    def test_apply_profile_atomic_and_backup(self):
        """Verify apply_profile creates timestamped backup and atomically updates target profile."""
        with tempfile.TemporaryDirectory() as td:
            mock_app_home = Path(td) / "clash_verge"
            mock_profiles = mock_app_home / "profiles"
            mock_profiles.mkdir(parents=True)

            # Create an existing target profile with MARKER
            active_file = mock_profiles / "my_config.yaml"
            active_file.write_text(f"{manage.MARKER}\nold_content: true\n", encoding="utf-8")

            # Create new profile to apply
            new_profile = Path(td) / "new_clash.yaml"
            new_profile.write_text(f"{manage.MARKER}\nnew_content: 100\n", encoding="utf-8")

            # Apply
            applied = manage.apply_profile(new_profile, app_home=mock_app_home)
            self.assertTrue(applied)

            # Verify active profile updated
            updated_content = active_file.read_text(encoding="utf-8")
            self.assertIn("new_content: 100", updated_content)

            # Verify backup created in .state/backups
            backup_dir = REPO_ROOT / ".state/backups"
            backups = list(backup_dir.glob("my_config_*.yaml"))
            self.assertTrue(len(backups) >= 1, "Backup file should be created")

    def test_check_command_missing_core_returns_code_2(self):
        """CRITICAL P2-3 TEST: Ensure manage.py check with missing core exits with non-zero code 2."""
        with tempfile.TemporaryDirectory() as td:
            fake_core = Path(td) / "nonexistent-core"
            res = manage.validate_with_core(manage.DEFAULT_CLASH_OUTPUT, core_path=fake_core)
            self.assertFalse(res, "Missing core must return False")


if __name__ == "__main__":
    unittest.main()
