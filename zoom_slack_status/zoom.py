from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Optional


MEETING_PROCESS_NAMES = ("CptHost",)
ZOOM_APP_PROCESS_NAMES = ("zoom.us", "Zoom Workplace")


@dataclass(frozen=True)
class ZoomMeetingState:
    in_meeting: bool
    signal: str
    detail: str


class ZoomDetector:
    def __init__(
        self,
        *,
        meeting_process_names: Iterable[str] = MEETING_PROCESS_NAMES,
        app_process_names: Iterable[str] = ZOOM_APP_PROCESS_NAMES,
    ) -> None:
        self.meeting_process_names = tuple(meeting_process_names)
        self.app_process_names = tuple(app_process_names)

    def detect(self) -> ZoomMeetingState:
        meeting_process = self._first_running_process(self.meeting_process_names)
        if meeting_process:
            return ZoomMeetingState(True, "process", f"{meeting_process} is running")

        applescript_state = self._detect_with_applescript()
        if applescript_state is not None:
            return applescript_state

        app_process = self._first_running_process(self.app_process_names)
        if app_process:
            return ZoomMeetingState(False, "process", f"{app_process} is running, no meeting detected")

        return ZoomMeetingState(False, "process", "Zoom is not running")

    def _first_running_process(self, process_names: Iterable[str]) -> Optional[str]:
        for process_name in process_names:
            if self._process_running(process_name):
                return process_name
        return None

    @staticmethod
    def _process_running(process_name: str) -> bool:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", process_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        return result.returncode == 0

    def _detect_with_applescript(self) -> Optional[ZoomMeetingState]:
        script = _applescript(self.app_process_names)
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
            return None

        if result.returncode != 0:
            return None

        output = result.stdout.strip()
        if output.startswith("active:"):
            return ZoomMeetingState(True, "applescript", output[len("active:") :])
        if output.startswith("idle:"):
            return ZoomMeetingState(False, "applescript", output[len("idle:") :])
        return None


def _applescript(app_process_names: Iterable[str]) -> str:
    names = ", ".join(f'"{name}"' for name in app_process_names)
    return f'''
tell application "System Events"
    set zoomNames to {{{names}}}
    repeat with zoomName in zoomNames
        if exists process (zoomName as text) then
            tell process (zoomName as text)
                try
                    repeat with barItem in menu bar items of menu bar 1
                        try
                            set menuItemNames to name of menu items of menu 1 of barItem
                            if menuItemNames contains "Leave Meeting" then return "active:menu Leave Meeting"
                            if menuItemNames contains "End Meeting" then return "active:menu End Meeting"
                        end try
                    end repeat
                end try
                try
                    repeat with zoomWindow in windows
                        set windowName to name of zoomWindow as text
                        if windowName contains "Zoom Meeting" then return "active:window " & windowName
                        if windowName contains "Breakout Rooms" then return "active:window " & windowName
                        if windowName is "Meeting" then return "active:window " & windowName
                    end repeat
                end try
            end tell
        end if
    end repeat
    return "idle:Zoom UI checked"
end tell
'''

