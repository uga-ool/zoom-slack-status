import unittest
from unittest import mock

from zoom_slack_status.outlook_calendar import OutlookCalendarDetector, _applescript, _escape


class ApplescriptBuilderTest(unittest.TestCase):
    def test_includes_subject_clause_for_each_label(self):
        script = _applescript("Microsoft Outlook", ("lunch", "team lunch"))
        self.assertIn('subject contains "lunch"', script)
        self.assertIn('subject contains "team lunch"', script)
        self.assertIn(" or ", script)

    def test_escape_strips_quotes(self):
        self.assertEqual(_escape('lunch "break"'), "lunch break")

    def test_filters_by_time_of_day_not_original_recurrence_date(self):
        script = _applescript("Microsoft Outlook", ("lunch",))
        self.assertIn("time of (current date)", script)
        self.assertIn("time of (start time of e)", script)
        self.assertNotIn("whose start time", script)


class OutlookCalendarDetectorTest(unittest.TestCase):
    def test_parses_delimited_titles(self):
        detector = OutlookCalendarDetector()
        fake_result = mock.Mock(returncode=0, stdout="Lunch\x1fTeam Lunch\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_result):
            check = detector.current_event_titles()
        self.assertTrue(check.checked)
        self.assertEqual(check.event_titles, ("Lunch", "Team Lunch"))

    def test_empty_output_means_no_active_events(self):
        detector = OutlookCalendarDetector()
        fake_result = mock.Mock(returncode=0, stdout="\n", stderr="")
        with mock.patch("subprocess.run", return_value=fake_result):
            check = detector.current_event_titles()
        self.assertTrue(check.checked)
        self.assertEqual(check.event_titles, ())

    def test_nonzero_returncode_marks_unchecked(self):
        detector = OutlookCalendarDetector()
        fake_result = mock.Mock(returncode=1, stdout="", stderr="not authorized")
        with mock.patch("subprocess.run", return_value=fake_result):
            check = detector.current_event_titles()
        self.assertFalse(check.checked)
        self.assertEqual(check.detail, "not authorized")

    def test_subprocess_error_marks_unchecked(self):
        detector = OutlookCalendarDetector()
        with mock.patch("subprocess.run", side_effect=OSError("no osascript")):
            check = detector.current_event_titles()
        self.assertFalse(check.checked)


if __name__ == "__main__":
    unittest.main()
