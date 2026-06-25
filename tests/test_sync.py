import tempfile
import unittest
from pathlib import Path

from zoom_slack_status.config import DEFAULT_OUTLOOK_STATUS_TEXTS, Config, _parse_outlook_status_texts
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
    )


class StatusSynchronizerTest(unittest.TestCase):
    def test_sets_and_restores_previous_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            previous = SlackStatus("Lunch", ":fork_and_knife:", 0)
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
            outlook_status = SlackStatus("In a meeting", ":calendar:", 0)
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
            self.assertFalse(state.managed)
            self.assertTrue(state.suppressed_until_idle)

    def test_clears_deferred_outlook_state_when_zoom_idle(self):
        with tempfile.TemporaryDirectory() as tmp:
            outlook_status = SlackStatus("In a meeting", ":calendar:", 0)
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
            outlook_status = SlackStatus("In a meeting", ":calendar:", 0)
            slack.status = outlook_status

            sync.handle_zoom_state(False, now=120)
            self.assertEqual(slack.status, outlook_status)

    def test_sets_zoom_status_when_outlook_precedence_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            outlook_status = SlackStatus("In a meeting", ":calendar:", 0)
            slack = FakeSlack(outlook_status)
            sync = StatusSynchronizer(
                make_config(Path(tmp) / "state.json", ttl_minutes=0, outlook_precedence=False),
                slack,
                StateStore(Path(tmp) / "state.json"),
            )

            sync.handle_zoom_state(True, now=100)

            self.assertEqual(slack.status, SlackStatus("On a Zoom call", ":video_camera:", 0))
            self.assertEqual(len(slack.set_calls), 1)


if __name__ == "__main__":
    unittest.main()

