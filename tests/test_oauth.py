import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status.oauth import OAuthToken, code_challenge, refresh_access_token
from zoom_slack_status.oauth import save_oauth_env, save_refreshed_token, update_env_file


def _fake_urlopen_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Response:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    return _Response()


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

    def test_save_oauth_env_persists_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("", encoding="utf-8")
            token = OAuthToken(
                access_token="xoxp-access",
                token_type="user",
                user_id="U1",
                team_id="T1",
                refresh_token="xoxe-refresh",
                expires_in=3600,
            )

            save_oauth_env(env_path, client_id="C1", redirect_uri="http://localhost:8765/cb", token=token)

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("SLACK_REFRESH_TOKEN=xoxe-refresh", content)
            self.assertIn("SLACK_TOKEN_EXPIRES_AT=", content)

    def test_refresh_access_token_parses_top_level_response(self):
        response_payload = {
            "ok": True,
            "access_token": "xoxp-new",
            "refresh_token": "xoxe-new-refresh",
            "token_type": "user",
            "expires_in": 43200,
        }
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_urlopen_response(response_payload)
        ):
            token = refresh_access_token(client_id="C1", refresh_token="xoxe-old-refresh")

        self.assertEqual(token.access_token, "xoxp-new")
        self.assertEqual(token.refresh_token, "xoxe-new-refresh")
        self.assertEqual(token.expires_in, 43200)

    def test_refresh_access_token_keeps_old_refresh_token_if_none_returned(self):
        response_payload = {"ok": True, "access_token": "xoxp-new", "expires_in": 43200}
        with mock.patch(
            "urllib.request.urlopen", return_value=_fake_urlopen_response(response_payload)
        ):
            token = refresh_access_token(client_id="C1", refresh_token="xoxe-old-refresh")

        self.assertEqual(token.refresh_token, "xoxe-old-refresh")

    def test_save_refreshed_token_updates_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("SLACK_USER_TOKEN=old\nSLACK_REFRESH_TOKEN=old-refresh\n", encoding="utf-8")
            token = OAuthToken(
                access_token="xoxp-new",
                token_type="user",
                user_id="U1",
                team_id="T1",
                refresh_token="xoxe-new-refresh",
                expires_in=3600,
            )

            save_refreshed_token(env_path, token)

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("SLACK_USER_TOKEN=xoxp-new", content)
            self.assertIn("SLACK_REFRESH_TOKEN=xoxe-new-refresh", content)


if __name__ == "__main__":
    unittest.main()
