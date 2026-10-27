import io
import json
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from zoom_slack_status.slack import (
    SlackApiError,
    SlackAuthError,
    SlackClient,
    SlackRateLimitError,
    SlackStatus,
)


def response(body):
    fake = mock.MagicMock()
    fake.read.return_value = json.dumps(body).encode("utf-8")
    fake.__enter__.return_value = fake
    return fake


def http_error(code, headers=None, body=b""):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://slack.com/api/x", code, "error", message, io.BytesIO(body))


class FakeRefresher:
    def __init__(self, *, needs_refresh=False, new_token="xoxp-new"):
        self._needs_refresh = needs_refresh
        self.new_token = new_token
        self.refresh_calls = 0

    def needs_refresh(self):
        return self._needs_refresh

    def refresh(self):
        self.refresh_calls += 1
        self._needs_refresh = False
        return self.new_token


def sent_token(urlopen_mock, call_index=0):
    request = urlopen_mock.call_args_list[call_index].args[0]
    return request.get_header("Authorization")


@mock.patch("zoom_slack_status.slack.urllib.request.urlopen")
class SlackClientTest(unittest.TestCase):
    def test_get_status_parses_profile(self, urlopen):
        urlopen.return_value = response(
            {"ok": True, "profile": {"status_text": "Lunch", "status_emoji": ":taco:", "status_expiration": 5}}
        )

        status = SlackClient("xoxp-1").get_status()

        self.assertEqual(status, SlackStatus("Lunch", ":taco:", 5))
        self.assertEqual(sent_token(urlopen), "Bearer xoxp-1")

    def test_ok_false_raises_api_error(self, urlopen):
        urlopen.return_value = response({"ok": False, "error": "profile_set_failed"})

        with self.assertRaises(SlackApiError) as ctx:
            SlackClient("xoxp-1").set_status(SlackStatus("x", ":x:", 0))
        self.assertNotIsInstance(ctx.exception, SlackAuthError)
        self.assertIn("profile_set_failed", str(ctx.exception))

    def test_auth_error_without_refresher_is_raised(self, urlopen):
        urlopen.return_value = response({"ok": False, "error": "token_expired"})

        with self.assertRaises(SlackAuthError) as ctx:
            SlackClient("xoxp-1").get_status()
        self.assertEqual(ctx.exception.error, "token_expired")

    def test_rate_limit_reads_retry_after(self, urlopen):
        urlopen.side_effect = http_error(429, {"Retry-After": "30"})

        with self.assertRaises(SlackRateLimitError) as ctx:
            SlackClient("xoxp-1").get_status()
        self.assertEqual(ctx.exception.retry_after, 30)

    def test_http_error_raises_api_error(self, urlopen):
        urlopen.side_effect = http_error(500, body=b"oops")

        with self.assertRaisesRegex(SlackApiError, "HTTP 500"):
            SlackClient("xoxp-1").get_status()

    def test_network_error_raises_api_error(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("offline")

        with self.assertRaisesRegex(SlackApiError, "Could not reach Slack"):
            SlackClient("xoxp-1").get_status()

    def test_dry_run_makes_no_requests(self, urlopen):
        client = SlackClient("", dry_run=True)

        client.set_status(SlackStatus("On a Zoom call", ":video_camera:", 0))

        self.assertEqual(client.get_status().text, "On a Zoom call")
        urlopen.assert_not_called()

    def test_refreshes_before_request_when_token_expires_soon(self, urlopen):
        urlopen.return_value = response({"ok": True, "profile": {}})
        refresher = FakeRefresher(needs_refresh=True)

        SlackClient("xoxp-old", token_refresher=refresher).get_status()

        self.assertEqual(refresher.refresh_calls, 1)
        self.assertEqual(sent_token(urlopen), "Bearer xoxp-new")

    def test_refreshes_and_retries_once_on_expired_token(self, urlopen):
        urlopen.side_effect = [
            response({"ok": False, "error": "token_expired"}),
            response({"ok": True, "profile": {"status_text": "Lunch"}}),
        ]
        refresher = FakeRefresher()

        status = SlackClient("xoxp-old", token_refresher=refresher).get_status()

        self.assertEqual(status.text, "Lunch")
        self.assertEqual(refresher.refresh_calls, 1)
        self.assertEqual(sent_token(urlopen, 1), "Bearer xoxp-new")

    def test_revoked_token_is_not_refreshed(self, urlopen):
        urlopen.return_value = response({"ok": False, "error": "token_revoked"})
        refresher = FakeRefresher()

        with self.assertRaises(SlackAuthError):
            SlackClient("xoxp-old", token_refresher=refresher).get_status()
        self.assertEqual(refresher.refresh_calls, 0)


if __name__ == "__main__":
    unittest.main()
