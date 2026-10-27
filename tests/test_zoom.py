import subprocess
import unittest
from unittest import mock

from zoom_slack_status.zoom import ZoomDetector


def completed(returncode=0, stdout=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def fake_run(running=(), applescript=None):
    """Build a subprocess.run stand-in. applescript is stdout, an exception, or None for a failed run."""

    def run(args, **_kwargs):
        if args[0] == "/usr/bin/pgrep":
            return completed(0 if args[-1] in running else 1)
        if isinstance(applescript, Exception):
            raise applescript
        if applescript is None:
            return completed(1)
        return completed(0, applescript)

    return run


@mock.patch("zoom_slack_status.zoom.subprocess.run")
class ZoomDetectorTest(unittest.TestCase):
    def test_meeting_process_means_in_meeting_without_applescript(self, run):
        run.side_effect = fake_run(running={"CptHost"})

        state = ZoomDetector().detect()

        self.assertTrue(state.in_meeting)
        self.assertEqual(state.signal, "process")
        self.assertTrue(all(call.args[0][0] == "/usr/bin/pgrep" for call in run.call_args_list))

    def test_applescript_menu_signal_detects_meeting(self, run):
        run.side_effect = fake_run(running={"zoom.us"}, applescript="active:menu Leave Meeting\n")

        state = ZoomDetector().detect()

        self.assertTrue(state.in_meeting)
        self.assertEqual((state.signal, state.detail), ("applescript", "menu Leave Meeting"))

    def test_applescript_idle_is_trusted(self, run):
        run.side_effect = fake_run(running={"zoom.us"}, applescript="idle:Zoom UI checked")

        state = ZoomDetector().detect()

        self.assertFalse(state.in_meeting)
        self.assertEqual(state.signal, "applescript")

    def test_falls_back_to_app_process_when_applescript_fails(self, run):
        run.side_effect = fake_run(running={"zoom.us"}, applescript=None)

        state = ZoomDetector().detect()

        self.assertFalse(state.in_meeting)
        self.assertEqual(state.detail, "zoom.us is running, no meeting detected")

    def test_applescript_timeout_is_treated_as_inconclusive(self, run):
        run.side_effect = fake_run(applescript=subprocess.TimeoutExpired("osascript", 5))

        state = ZoomDetector().detect()

        self.assertFalse(state.in_meeting)
        self.assertEqual(state.detail, "Zoom is not running")

    def test_unexpected_applescript_output_is_inconclusive(self, run):
        run.side_effect = fake_run(running={"Zoom Workplace"}, applescript="something else")

        state = ZoomDetector().detect()

        self.assertEqual(state.detail, "Zoom Workplace is running, no meeting detected")


if __name__ == "__main__":
    unittest.main()
