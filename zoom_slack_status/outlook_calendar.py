from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable


TITLE_DELIMITER = "\x1f"


@dataclass(frozen=True)
class CalendarCheck:
    checked: bool
    event_titles: tuple[str, ...] = ()
    detail: str = ""


class OutlookCalendarDetector:
    """Reads currently-active calendar event titles from Outlook for Mac via AppleScript.

    Outlook's AppleScript dictionary exposes a recurring event's "start time"
    and "end time" as its *original* occurrence (e.g. when the series was
    created), not today's occurrence. Filtering calendar events by that date
    range would silently miss recurring events like a daily "Lunch" block.
    Instead, this matches events by subject substring, then compares the
    time-of-day (seconds since midnight) of that event's start/end against
    the current time-of-day, which is stable across recurrences.

    Only classic Outlook for Mac exposes an AppleScript dictionary. If the
    installed Outlook does not support scripting, or Outlook is not running,
    `current_event_titles()` returns `checked=False` and callers should treat
    that as "unknown" rather than "no lunch event."
    """

    def __init__(self, *, app_name: str = "Microsoft Outlook", subject_filters: Iterable[str] = ("Lunch",)) -> None:
        self.app_name = app_name
        self.subject_filters = tuple(subject_filters) or ("Lunch",)

    def current_event_titles(self) -> CalendarCheck:
        script = _applescript(self.app_name, self.subject_filters)
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-e", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return CalendarCheck(False, (), "osascript failed to run")

        if result.returncode != 0:
            return CalendarCheck(False, (), result.stderr.strip() or "osascript returned an error")

        output = result.stdout.strip()
        if not output:
            return CalendarCheck(True, ())
        titles = tuple(title.strip() for title in output.split(TITLE_DELIMITER) if title.strip())
        return CalendarCheck(True, titles)


def _applescript(app_name: str, subject_filters: Iterable[str]) -> str:
    subject_clause = " or ".join(
        f'subject contains "{_escape(label)}"' for label in subject_filters
    )
    return f'''
set titleList to {{}}
try
    if application "{app_name}" is running then
        tell application "{app_name}"
            set nowSeconds to time of (current date)
            set cals to calendars
            repeat with cal in cals
                try
                    set evts to (calendar events of cal whose {subject_clause})
                    repeat with e in evts
                        try
                            set startSeconds to time of (start time of e)
                            set endSeconds to time of (end time of e)
                            set isActive to false
                            if startSeconds ≤ endSeconds then
                                if nowSeconds ≥ startSeconds and nowSeconds < endSeconds then set isActive to true
                            else
                                if nowSeconds ≥ startSeconds or nowSeconds < endSeconds then set isActive to true
                            end if
                            if isActive then set end of titleList to (subject of e)
                        end try
                    end repeat
                end try
            end repeat
        end tell
    end if
end try
set AppleScript's text item delimiters to (ASCII character 31)
set outputText to titleList as text
set AppleScript's text item delimiters to ""
return outputText
'''


def _escape(label: str) -> str:
    return label.replace('"', "")
