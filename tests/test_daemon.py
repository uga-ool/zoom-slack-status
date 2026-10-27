import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status import __main__ as cli
from zoom_slack_status.slack import SlackApiError, SlackRateLimitError
from zoom_slack_status.zoom import ZoomMeetingState

from test_sync import make_config


class StopLoop(Exception):
    pass


class FakeDetector:
    def __init__(self, states):
        self.states = list(states)

    def detect(self):
        in_meeting = self.states.pop(0)
        return ZoomMeetingState(in_meeting, "test", "scripted")


class RecordingSynchronizer:
    def __init__(self, errors=None):
        self.calls = []
        self.errors = list(errors or [])

    def handle_zoom_state(self, in_meeting):
        self.calls.append(in_meeting)
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error


def run_polls(config, states, synchronizer):
    """Run the daemon loop for len(states) polls and return the sleep durations it requested."""
    sleeps = []
    polls_left = [len(states)]

    def sleep(seconds):
        sleeps.append(seconds)
        if seconds == config.poll_seconds:
            polls_left[0] -= 1
            if polls_left[0] == 0:
                raise StopLoop

    with mock.patch.object(cli.time, "sleep", side_effect=sleep):
        try:
            cli.run_daemon(config, FakeDetector(states), synchronizer)
        except StopLoop:
            pass
    return sleeps


class RunDaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # make_config uses busy_confirmations=1, idle_confirmations=2, poll_seconds=15.
        self.config = make_config(Path(self.tmp.name) / "state.json")

    def test_waits_for_idle_confirmations_before_reporting_idle(self):
        synchronizer = RecordingSynchronizer()

        run_polls(self.config, [True, False, False], synchronizer)

        self.assertEqual(synchronizer.calls, [True, True, False])

    def test_brief_idle_blip_does_not_end_the_call(self):
        synchronizer = RecordingSynchronizer()

        run_polls(self.config, [True, False, True, False], synchronizer)

        self.assertEqual(synchronizer.calls, [True, True, True, True])

    def test_no_slack_calls_until_first_confirmed_state(self):
        synchronizer = RecordingSynchronizer()

        run_polls(self.config, [False, False], synchronizer)

        self.assertEqual(synchronizer.calls, [False])

    def test_rate_limit_sleeps_for_retry_after(self):
        synchronizer = RecordingSynchronizer(errors=[SlackRateLimitError(42)])

        sleeps = run_polls(self.config, [True], synchronizer)

        self.assertEqual(sleeps, [42, 15])

    def test_slack_error_backs_off_at_least_a_minute(self):
        synchronizer = RecordingSynchronizer(errors=[SlackApiError("boom")])

        sleeps = run_polls(self.config, [True], synchronizer)

        self.assertEqual(sleeps, [60, 15])


class TokenOwnershipTest(unittest.TestCase):
    def test_only_installed_copy_refreshes_once_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "staged.env"
            repo = Path(tmp) / "repo.env"
            with mock.patch.object(cli, "APP_ENV_PATH", staged):
                self.assertTrue(cli._owns_refresh_token(repo))

                staged.write_text("", encoding="utf-8")
                self.assertFalse(cli._owns_refresh_token(repo))
                self.assertTrue(cli._owns_refresh_token(staged))

    def test_slack_client_gets_refresher_only_when_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp) / "state.json")
            config = config.__class__(
                **{**config.__dict__, "slack_client_id": "123.456", "slack_refresh_token": "xoxe-1-a"}
            )
            args = argparse.Namespace(env_file=str(Path(tmp) / ".env"))
            with mock.patch.object(cli, "APP_ENV_PATH", Path(tmp) / "missing.env"):
                self.assertIsNotNone(cli._slack_client(config, args).token_refresher)

                dry_run = config.__class__(**{**config.__dict__, "dry_run": True})
                self.assertIsNone(cli._slack_client(dry_run, args).token_refresher)


if __name__ == "__main__":
    unittest.main()
