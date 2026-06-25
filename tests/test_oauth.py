import tempfile
import unittest
from pathlib import Path

from zoom_slack_status.oauth import code_challenge, update_env_file


class OAuthTest(unittest.TestCase):
    def test_code_challenge_matches_pkce_example(self):
        self.assertEqual(
            code_challenge("secretpassword"),
            "ldMBaaWcQYtSATMV_IG8mf3wp7A6EW80arYoSW80ntU",
        )

    def test_update_env_file_preserves_comments_and_replaces_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# hello\nSLACK_CLIENT_ID=old\nZOOM_SLACK_STATUS_TEXT=On a Zoom call\n",
                encoding="utf-8",
            )

            update_env_file(
                env_path,
                {
                    "SLACK_CLIENT_ID": "new",
                    "SLACK_USER_TOKEN": "xoxp-secret",
                },
            )

            self.assertEqual(
                env_path.read_text(encoding="utf-8"),
                "# hello\n"
                "SLACK_CLIENT_ID=new\n"
                "ZOOM_SLACK_STATUS_TEXT=On a Zoom call\n"
                "SLACK_USER_TOKEN=xoxp-secret\n",
            )
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
