import tempfile
import unittest
from pathlib import Path

from zoom_slack_status.config import DEFAULT_LUNCH_LABELS, DEFAULT_OUTLOOK_STATUS_TEXTS
from zoom_slack_status.config import Config, _parse_outlook_status_texts
from zoom_slack_status.slack import SlackStatus
from zoom_slack_status.sync import StateStore, StatusSynchronizer


class FakeSlack:
    def __init__(self, status):
        self.status = status
        self.set_calls = []

    def get_status(self):
        return self.status

    def set_status(self, status):
        self.status = status
        self.set_calls.append(status)


def make_config(
    state_file,
    ttl_minutes=120,
    refresh_minutes=30,
    *,
    outlook_precedence=True,
    outlook_status_texts=DEFAULT_OUTLOOK_STATUS_TEXTS,
    lunch_enabled=True,
    lunch_status_text="Lunch",
    lunch_status_emoji=":fork_and_knife:",
    lunch_labels=DEFAULT_LUNCH_LABELS,
    lunch_ttl_minutes=60,
):
    return Config(
        slack_user_token="token",
        status_text="On a Zoom call",
        status_emoji=":video_camera:",
        status_ttl_minutes=ttl_minutes,
        refresh_minutes=refresh_minutes,
        poll_seconds=15,
        busy_confirmations=1,
        idle_confirmations=2,
        state_file=Path(state_file),
        dry_run=False,
        outlook_precedence=outlook_precedence,
        outlook_status_texts=_parse_outlook_status_texts(outlook_status_texts),
        lunch_enabled=lunch_enabled,
        lunch_status_text=lunch_status_text,
        lunch_status_emoji=lunch_status_emoji,
        lunch_labels=_parse_outlook_status_texts(lunch_labels),
        lunch_ttl_minutes=lunch_ttl_minutes,
        outlook_app_name="Microsoft Outlook",
        slack_client_id="",
        slack_refresh_token="",
        slack_token_expires_at=0,
    )


class StatusSynchronizerTest(unittest.TestCase):
    def test_sets_and_restores_previous_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = SlackStatus("Heads down", ":memo:", 0)
            slack = FakeSlack(previous)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            self.assertEqual(slack.status, SlackStatus("On a Zoom call", ":video_camera:", 0))

            sync.handle_zoom_state(False, now=120)
            self.assertEqual(slack.status, previous)

    def test_skips_restore_when_status_was_changed_manually(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            manual = SlackStatus("Heads down", ":memo:", 0)
            slack.status = manual

            sync.handle_zoom_state(False, now=120)
            self.assertEqual(slack.status, manual)

    def test_previous_expired_status_restores_to_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = SlackStatus("Back soon", ":coffee:", 110)
            slack = FakeSlack(previous)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            sync.handle_zoom_state(False, now=120)

            self.assertEqual(slack.status, SlackStatus("", "", 0))

    def test_refresh_extends_expiration(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=120, refresh_minutes=30),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            first_expiration = slack.status.expiration
            sync.handle_zoom_state(True, now=100 + 31 * 60)

            self.assertGreater(slack.status.expiration, first_expiration)
            self.assertEqual(len(slack.set_calls), 2)

    def test_defers_when_outlook_status_present_at_meeting_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            outlook_status = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
            slack = FakeSlack(outlook_status)
            store = StateStore(Path(tmp) / "state.json")
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                store,
            )

            sync.handle_zoom_state(True, now=100)

            self.assertEqual(slack.status, outlook_status)
            self.assertEqual(slack.set_calls, [])
            state = store.load()
            self.assertFalse(state.zoom.managed)
            self.assertTrue(state.zoom.suppressed_until_idle)

    def test_clears_deferred_outlook_state_when_zoom_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            outlook_status = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
            slack = FakeSlack(outlook_status)
            store = StateStore(Path(tmp) / "state.json")
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                store,
            )

            sync.handle_zoom_state(True, now=100)
            sync.handle_zoom_state(False, now=120)

            self.assertEqual(slack.status, outlook_status)
            self.assertEqual(slack.set_calls, [])
            self.assertFalse(store.path.exists())

    def test_skips_restore_when_status_becomes_outlook_like_during_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            outlook_status = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
            slack.status = outlook_status

            sync.handle_zoom_state(False, now=120)
            self.assertEqual(slack.status, outlook_status)

    def test_sets_zoom_status_when_outlook_precedence_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            outlook_status = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
            slack = FakeSlack(outlook_status)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0, outlook_precedence=False),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)

            self.assertEqual(slack.status, SlackStatus("On a Zoom call", ":video_camera:", 0))
            self.assertEqual(len(slack.set_calls), 1)

    def test_sets_lunch_status_when_calendar_event_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = SlackStatus("In a meeting • Outlook Calendar", ":spiral_calendar_pad:", 0)
            slack = FakeSlack(previous)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json"),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)

            self.assertEqual(slack.status, SlackStatus("Lunch", ":fork_and_knife:", 100 + 60 * 60))

    def test_lunch_status_restores_previous_when_event_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = SlackStatus("", "", 0)
            slack = FakeSlack(previous)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json"),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)
            sync.handle_lunch_state(False, now=2000)

            self.assertEqual(slack.status, previous)

    def test_lunch_status_stops_tracking_after_manual_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json"),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)
            manual = SlackStatus("Heads down", ":memo:", 0)
            slack.status = manual

            sync.handle_lunch_state(True, now=200)

            self.assertEqual(slack.status, manual)

    def test_lunch_status_expiration_refreshes_while_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", lunch_ttl_minutes=60),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)
            first_expiration = slack.status.expiration
            sync.handle_lunch_state(True, now=first_expiration - 60)

            self.assertGreater(slack.status.expiration, first_expiration)
            self.assertEqual(len(slack.set_calls), 2)

    def test_zoom_call_defers_to_active_lunch_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)
            sync.handle_zoom_state(True, now=110)

            self.assertEqual(slack.status, SlackStatus("Lunch", ":fork_and_knife:", 100 + 60 * 60))
            self.assertEqual(len(slack.set_calls), 1)

    def test_lunch_detection_ignored_while_zoom_managed(self):
        with tempfile.TemporaryDirectory() as tmp:
            slack = FakeSlack(SlackStatus("", "", 0))
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)
            sync.handle_lunch_state(True, now=110)

            self.assertEqual(slack.status, SlackStatus("On a Zoom call", ":video_camera:", 0))
            self.assertEqual(len(slack.set_calls), 1)

    def test_lunch_detection_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = SlackStatus("", "", 0)
            slack = FakeSlack(original)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", lunch_enabled=False),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_lunch_state(True, now=100)

            self.assertEqual(slack.status, original)
            self.assertEqual(slack.set_calls, [])


if __name__ == "__main__":
    unittest.main()
