import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status.config import read_env_file
from zoom_slack_status.oauth import (
    OAuthToken,
    TokenRefresher,
    exchange_code_for_token,
    refresh_access_token,
    save_oauth_env,
)
from zoom_slack_status.slack import SlackApiError


def new_token(access="xoxe.xoxp-new", refresh="xoxe-1-new", expires_in=43200):
    return OAuthToken(
        access_token=access,
        token_type="user",
        user_id="U1",
        team_id="T1",
        refresh_token=refresh,
        expires_in=expires_in,
    )


class RefreshRequestTest(unittest.TestCase):
    @mock.patch("zoom_slack_status.oauth._oauth_request")
    def test_refresh_sends_pkce_refresh_grant_without_secret(self, oauth_request):
        oauth_request.return_value = {
            "ok": True,
            "access_token": "xoxe.xoxp-new",
            "refresh_token": "xoxe-1-new",
            "expires_in": 43200,
            "token_type": "user",
        }

        token = refresh_access_token(client_id="123.456", refresh_token="xoxe-1-old")

        oauth_request.assert_called_once_with(
            {"client_id": "123.456", "grant_type": "refresh_token", "refresh_token": "xoxe-1-old"}
        )
        self.assertEqual(token.access_token, "xoxe.xoxp-new")
        self.assertEqual(token.refresh_token, "xoxe-1-new")
        self.assertEqual(token.expires_in, 43200)

    @mock.patch("zoom_slack_status.oauth._oauth_request")
    def test_code_exchange_reads_nested_user_token(self, oauth_request):
        oauth_request.return_value = {
            "ok": True,
            "authed_user": {"id": "U1", "access_token": "xoxp-1", "refresh_token": "xoxe-1-a", "expires_in": 60},
            "team": {"id": "T1"},
        }

        token = exchange_code_for_token(client_id="c", redirect_uri="r", code="code", verifier="v")

        self.assertEqual((token.access_token, token.user_id, token.team_id), ("xoxp-1", "U1", "T1"))
        self.assertEqual(token.refresh_token, "xoxe-1-a")

    def test_save_oauth_env_stores_refresh_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"

            save_oauth_env(env_path, client_id="c", redirect_uri="r", token=new_token())

            values = read_env_file(env_path)
            self.assertEqual(values["SLACK_REFRESH_TOKEN"], "xoxe-1-new")
            self.assertTrue(values["SLACK_TOKEN_EXPIRES_AT"].isdigit())


class TokenRefresherTest(unittest.TestCase):
    def make_refresher(self, env_path, *, expires_at=0, refresh=None):
        return TokenRefresher(
            env_file=env_path,
            client_id="123.456",
            refresh_token="xoxe-1-old",
            expires_at=expires_at,
            refresh=refresh or mock.Mock(return_value=new_token()),
        )

    def write_env(self, env_path, refresh_token="xoxe-1-old"):
        env_path.write_text(
            "SLACK_CLIENT_ID=123.456\n"
            "SLACK_USER_TOKEN=xoxe.xoxp-old\n"
            f"SLACK_REFRESH_TOKEN={refresh_token}\n"
            "SLACK_TOKEN_EXPIRES_AT=100\n"
            "ZOOM_SLACK_STATUS_TEXT=On a Zoom call\n",
            encoding="utf-8",
        )

    def test_needs_refresh_only_near_expiry(self):
        refresher = self.make_refresher(Path("unused"), expires_at=10_000)

        self.assertFalse(refresher.needs_refresh(now=9_000))
        self.assertTrue(refresher.needs_refresh(now=9_500))
        self.assertFalse(self.make_refresher(Path("unused"), expires_at=0).needs_refresh(now=9_500))

    def test_refresh_writes_new_tokens_and_keeps_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            self.write_env(env_path)
            refresh = mock.Mock(return_value=new_token())
            refresher = self.make_refresher(env_path, refresh=refresh)

            access_token = refresher.refresh()

            refresh.assert_called_once_with(client_id="123.456", refresh_token="xoxe-1-old")
            self.assertEqual(access_token, "xoxe.xoxp-new")
            values = read_env_file(env_path)
            self.assertEqual(values["SLACK_USER_TOKEN"], "xoxe.xoxp-new")
            self.assertEqual(values["SLACK_REFRESH_TOKEN"], "xoxe-1-new")
            self.assertGreater(int(values["SLACK_TOKEN_EXPIRES_AT"]), time.time())
            self.assertEqual(values["ZOOM_SLACK_STATUS_TEXT"], "On a Zoom call")
            self.assertEqual(refresher.refresh_token, "xoxe-1-new")
            self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_refresh_adopts_token_another_process_already_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            self.write_env(env_path, refresh_token="xoxe-1-from-doctor")
            future = int(time.time()) + 40_000
            env_path.write_text(
                env_path.read_text(encoding="utf-8").replace("SLACK_TOKEN_EXPIRES_AT=100", f"SLACK_TOKEN_EXPIRES_AT={future}"),
                encoding="utf-8",
            )
            refresh = mock.Mock()
            refresher = self.make_refresher(env_path, refresh=refresh)

            access_token = refresher.refresh()

            refresh.assert_not_called()
            self.assertEqual(access_token, "xoxe.xoxp-old")
            self.assertEqual(refresher.refresh_token, "xoxe-1-from-doctor")
            self.assertEqual(refresher.expires_at, future)

    def test_refresh_failure_tells_user_to_log_in_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            self.write_env(env_path)
            refresher = self.make_refresher(
                env_path, refresh=mock.Mock(side_effect=SlackApiError("Slack OAuth error: invalid_refresh_token"))
            )

            with self.assertRaisesRegex(SlackApiError, "Run oauth login again"):
                refresher.refresh()
            self.assertEqual(read_env_file(env_path)["SLACK_REFRESH_TOKEN"], "xoxe-1-old")


if __name__ == "__main__":
    unittest.main()
