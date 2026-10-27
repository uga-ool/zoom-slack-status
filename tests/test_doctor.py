import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status import doctor
from zoom_slack_status.doctor import Check, format_checks, run_checks
from zoom_slack_status.outlook_calendar import CalendarCheck
from zoom_slack_status.slack import SlackApiError, SlackStatus
from zoom_slack_status.zoom import ZoomMeetingState

from test_sync import make_config


class FakeSlack:
    def __init__(self, status=None, error=None):
        self.status = status or SlackStatus("", "", 0)
        self.error = error

    def get_status(self):
        if self.error:
            raise self.error
        return self.status


class FakeCalendar:
    def __init__(self, calendar_check):
        self.calendar_check = calendar_check

    def current_event_titles(self):
        return self.calendar_check


class FakeDetector:
    def detect(self):
        return ZoomMeetingState(False, "process", "Zoom is not running")


def fake_run(*, accessibility_ok=True, agent_stdout="state = running", outlook_running=True):
    def run(args, **_kwargs):
        if args[0] == "/usr/bin/osascript":
            return subprocess.CompletedProcess(args, 0 if accessibility_ok else 1, "3", "")
        if args[0] == "/usr/bin/pgrep":
            return subprocess.CompletedProcess(args, 0 if outlook_running else 1, "", "")
        return subprocess.CompletedProcess(args, 0, agent_stdout, "")

    return run


class DoctorTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env_file = self.root / ".env"
        self.env_file.write_text("", encoding="utf-8")
        plist = self.root / "agent.plist"
        plist.write_text("", encoding="utf-8")
        patcher = mock.patch.multiple(doctor, PLIST_PATH=plist, LEGACY_PLIST_PATH=self.root / "legacy.plist")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calendar_check = CalendarCheck(True, ())

    def checks(self, *, config=None, slack=None, **run_kwargs):
        config = config or make_config(self.root / "state.json")
        return {
            check.name: check
            for check in run_checks(
                config,
                self.env_file,
                slack=slack if slack is not None else FakeSlack(SlackStatus("Lunch", ":taco:", 0)),
                detector=FakeDetector(),
                calendar_detector=FakeCalendar(self.calendar_check),
                run=fake_run(**run_kwargs),
                now=0,
            )
        }

    def test_all_good(self):
        checks = self.checks()

        self.assertTrue(all(check.ok for check in checks.values()), checks)
        self.assertIn(":taco: Lunch", checks["Slack"].detail)

    def test_slack_error_suggests_logging_in(self):
        checks = self.checks(slack=FakeSlack(error=SlackApiError("Slack API error from users.profile.get: invalid_auth")))

        self.assertFalse(checks["Slack"].ok)
        self.assertIn("oauth login", checks["Slack"].fix)

    def test_expiring_token_without_refresh_is_flagged(self):
        config = make_config(self.root / "state.json")
        config = config.__class__(**{**config.__dict__, "slack_token_expires_at": 7200})

        check = self.checks(config=config)["Slack"]

        self.assertFalse(check.ok)
        self.assertIn("about 2 hour(s)", check.detail)

    def test_missing_accessibility_is_optional(self):
        checks = self.checks(accessibility_ok=False)
        accessibility = checks["Accessibility (optional)"]

        self.assertFalse(accessibility.ok)
        self.assertFalse(accessibility.required)
        output = format_checks(list(checks.values()))
        self.assertIn("[--] Accessibility", output)
        self.assertIn("Everything looks good.", output)

    def test_unreadable_outlook_is_optional(self):
        self.calendar_check = CalendarCheck(False, (), "Outlook is not running")

        check = self.checks()["Outlook calendar for Lunch (optional)"]

        self.assertFalse(check.ok)
        self.assertFalse(check.required)
        self.assertIn("Outlook is not running", check.detail)

    def test_closed_outlook_is_not_reported_as_readable(self):
        check = self.checks(outlook_running=False)["Outlook calendar for Lunch (optional)"]

        self.assertFalse(check.ok)
        self.assertIn("Microsoft Outlook is not running", check.detail)

    def test_fix_lines_are_runnable_commands(self):
        checks = self.checks(slack=FakeSlack(error=SlackApiError("invalid_auth")), agent_stdout="state = not running")

        self.assertIn("/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env", checks["Slack"].fix)
        self.assertIn(
            "/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env",
            checks["Background watcher"].fix,
        )

    def test_outlook_check_skipped_when_lunch_disabled(self):
        checks = self.checks(config=make_config(self.root / "state.json", lunch_enabled=False))

        self.assertNotIn("Outlook calendar for Lunch (optional)", checks)

    def test_stopped_agent_is_a_problem(self):
        checks = self.checks(agent_stdout="state = not running")

        self.assertFalse(checks["Background watcher"].ok)

    def test_legacy_agent_asks_for_reinstall(self):
        doctor.LEGACY_PLIST_PATH.write_text("", encoding="utf-8")

        check = self.checks()["Background watcher"]

        self.assertFalse(check.ok)
        self.assertIn("older copy", check.detail)

    def test_format_counts_required_problems(self):
        output = format_checks([Check("Slack", False, "not connected", "Run oauth login."), Check("Zoom", True, "ok")])

        self.assertIn("[!!] Slack: not connected", output)
        self.assertIn("Fix: Run oauth login.", output)
        self.assertIn("1 problem(s) found.", output)


if __name__ == "__main__":
    unittest.main()
