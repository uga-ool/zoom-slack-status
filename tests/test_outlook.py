import unittest

from zoom_slack_status.config import DEFAULT_LUNCH_LABELS, DEFAULT_OUTLOOK_STATUS_TEXTS
from zoom_slack_status.config import _parse_outlook_status_texts
from zoom_slack_status.config import Config
from zoom_slack_status.outlook import any_lunch_event, is_lunch_event_title
from zoom_slack_status.outlook import is_outlook_calendar_status, outlook_status_label
from zoom_slack_status.slack import SlackStatus


def make_config(*, outlook_precedence=True, lunch_enabled=True):
    return Config(
        slack_user_token="token",
        status_text="On a Zoom call",
        status_emoji=":video_camera:",
        status_ttl_minutes=120,
        refresh_minutes=30,
        poll_seconds=15,
        busy_confirmations=1,
        idle_confirmations=2,
        state_file=__import__("pathlib").Path("/tmp/state.json"),
        dry_run=False,
        outlook_precedence=outlook_precedence,
        outlook_status_texts=_parse_outlook_status_texts(DEFAULT_OUTLOOK_STATUS_TEXTS),
        lunch_enabled=lunch_enabled,
        lunch_status_text="Lunch",
        lunch_status_emoji=":fork_and_knife:",
        lunch_labels=_parse_outlook_status_texts(DEFAULT_LUNCH_LABELS),
        lunch_ttl_minutes=60,
        outlook_app_name="Microsoft Outlook",
        slack_client_id="",
        slack_refresh_token="",
        slack_token_expires_at=0,
    )


class OutlookStatusTest(unittest.TestCase):
    def test_matches_plain_outlook_label(self):
        config = make_config()
        status = SlackStatus("In a meeting", ":calendar:", 0)
        self.assertTrue(is_outlook_calendar_status(status, config))

    def test_matches_slack_outlook_calendar_suffix(self):
        config = make_config()
        status = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
        self.assertTrue(is_outlook_calendar_status(status, config))

    def test_extracts_label_before_bullet(self):
        self.assertEqual(
            outlook_status_label("In a meeting • Outlook Calendar"),
            "in a meeting",
        )

    def test_does_not_match_zoom_status(self):
        config = make_config()
        status = SlackStatus("On a Zoom call", ":zoom:", 0)
        self.assertFalse(is_outlook_calendar_status(status, config))

    def test_respects_disabled_precedence(self):
        config = make_config(outlook_precedence=False)
        status = SlackStatus("In a meeting • Outlook Calendar", ":calendar:", 0)
        self.assertFalse(is_outlook_calendar_status(status, config))

    def test_matches_lunch_event_title(self):
        config = make_config()
        self.assertTrue(is_lunch_event_title("Lunch", config))

    def test_lunch_event_title_respects_case(self):
        config = make_config()
        self.assertTrue(is_lunch_event_title("lunch", config))

    def test_does_not_match_non_lunch_event_title(self):
        config = make_config()
        self.assertFalse(is_lunch_event_title("Team sync", config))

    def test_respects_disabled_lunch_detection(self):
        config = make_config(lunch_enabled=False)
        self.assertFalse(is_lunch_event_title("Lunch", config))

    def test_any_lunch_event_matches_one_of_several_titles(self):
        config = make_config()
        self.assertTrue(any_lunch_event(["Team sync", "Lunch", "1:1"], config))

    def test_any_lunch_event_false_when_none_match(self):
        config = make_config()
        self.assertFalse(any_lunch_event(["Team sync", "1:1"], config))


if __name__ == "__main__":
    unittest.main()
