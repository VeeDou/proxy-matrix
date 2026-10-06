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

                # Verify deploy token is saved securely (0600) and not printed
                deploy_token_file = manage.ROOT / ".state/deploy_token"
                self.assertTrue(deploy_token_file.is_file())
                tok = deploy_token_file.read_text(encoding="utf-8").strip()
                self.assertEqual(len(tok), 64)

                import stat
                mode_tok = stat.S_IMODE(deploy_token_file.stat().st_mode)
                self.assertEqual(mode_tok, 0o600)

                # Verify subscriptions/urls.json created with 0600
                urls_file = manage.ROOT / "subscriptions/urls.json"
                if urls_file.is_file():
                    mode_urls = stat.S_IMODE(urls_file.stat().st_mode)
                    self.assertEqual(mode_urls, 0o600)

                # Run init again without rotate_token -> must preserve same token (N-a)
                manage.init_project(rotate_token=False)
                tok_after = deploy_token_file.read_text(encoding="utf-8").strip()
                self.assertEqual(tok, tok_after)

                # Run init with rotate_token=True -> must generate new token
                manage.init_project(rotate_token=True)
                tok_rotated = deploy_token_file.read_text(encoding="utf-8").strip()
                self.assertNotEqual(tok, tok_rotated)
                self.assertEqual(len(tok_rotated), 64)
            finally:
                manage.ROOT = orig_root


if __name__ == "__main__":
    unittest.main()
