from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .config import Config
from .launch_agent import LABEL, LEGACY_PLIST_PATH, PLIST_PATH, _gui_target
from .outlook_calendar import OutlookCalendarDetector
from .slack import SlackApiError, SlackClient
from .zoom import ZoomDetector


CLI = "/usr/bin/python3 -m zoom_slack_status"
LOGIN_FIX = (
    f"From the project folder, run {CLI} oauth login --env-file .env "
    f"then {CLI} launch-agent install --env-file .env"
)
INSTALL_FIX = f"From the project folder, run {CLI} launch-agent install --env-file .env"
ERROR_LOG_PATH = Path("~/Library/Logs/zoom-slack-status.err.log").expanduser()
ACCESSIBILITY_SCRIPT = (
    'tell application "System Events" to count UI elements of '
    "(first application process whose frontmost is true)"
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    required: bool = True


def run_checks(
    config: Config,
    env_file: Path,
    *,
    slack: Optional[SlackClient],
    detector: Optional[ZoomDetector] = None,
    calendar_detector: Optional[OutlookCalendarDetector] = None,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    now: Optional[float] = None,
) -> List[Check]:
    now = time.time() if now is None else now
    checks = [
        _check_python(),
        _check_env_file(env_file),
        _check_slack(config, slack, now),
        _check_zoom(detector or ZoomDetector()),
        _check_accessibility(run),
    ]
    if config.lunch_enabled:
        calendar_detector = calendar_detector or OutlookCalendarDetector(
            app_name=config.outlook_app_name, subject_filters=config.lunch_labels
        )
        checks.append(_check_outlook_calendar(calendar_detector, config.outlook_app_name, run))
    checks.append(_check_launch_agent(run))
    return checks


def format_checks(checks: List[Check]) -> str:
    lines = []
    for check in checks:
        marker = "ok" if check.ok else ("!!" if check.required else "--")
        lines.append(f"[{marker}] {check.name}: {check.detail}")
        if not check.ok and check.fix:
            lines.append(f"     Fix: {check.fix}")
    problems = sum(1 for check in checks if not check.ok and check.required)
    lines.append("")
    if problems:
        lines.append(f"{problems} problem(s) found. Logs: {ERROR_LOG_PATH}")
    else:
        lines.append("Everything looks good.")
    return "\n".join(lines) + "\n"


def _check_python() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < (3, 9):
        return Check("Python", False, f"{version} at {sys.executable}", "Install Python 3.9 or newer.")
    return Check("Python", True, f"{version} at {sys.executable}")


def _check_env_file(env_file: Path) -> Check:
    if env_file.expanduser().exists():
        return Check("Settings file", True, str(env_file))
    return Check(
        "Settings file",
        False,
        f"{env_file} not found",
        "Copy .env.example to .env, or pass --env-file with the right path.",
    )


def _check_slack(config: Config, slack: Optional[SlackClient], now: float) -> Check:
    login_fix = LOGIN_FIX
    if slack is None or not config.slack_user_token:
        return Check("Slack", False, "not connected (SLACK_USER_TOKEN is empty)", login_fix)
    try:
        status = slack.get_status()
    except SlackApiError as exc:
        return Check("Slack", False, str(exc), login_fix)

    current = f"{status.emoji} {status.text}".strip() or "(no status set)"
    detail = f"connected; current status is {current}"
    if config.slack_refresh_token:
        detail += "; token refreshes automatically"
    elif config.slack_token_expires_at:
        hours = max(0, int((config.slack_token_expires_at - now) // 3600))
        detail += f"; token expires in about {hours} hour(s) and cannot refresh"
        return Check("Slack", False, detail, login_fix)
    return Check("Slack", True, detail)


def _check_zoom(detector: ZoomDetector) -> Check:
    state = detector.detect()
    meeting = "in a meeting" if state.in_meeting else "not in a meeting"
    return Check("Zoom", True, f"{meeting} (via {state.signal}: {state.detail})")


def _check_accessibility(run: Callable[..., subprocess.CompletedProcess]) -> Check:
    name = "Accessibility (optional)"
    try:
        result = run(
            ["/usr/bin/osascript", "-e", ACCESSIBILITY_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(name, False, f"could not run osascript ({exc})", required=False)
    if result.returncode == 0:
        return Check(name, True, "granted; Zoom menus can be read as a backup signal", required=False)
    return Check(
        name,
        False,
        "not granted; meeting detection relies on the Zoom process only",
        "Open System Settings > Privacy & Security > Accessibility and turn on the app running this check.",
        required=False,
    )


def _check_outlook_calendar(
    calendar_detector: OutlookCalendarDetector,
    app_name: str,
    run: Callable[..., subprocess.CompletedProcess],
) -> Check:
    name = "Outlook calendar for Lunch (optional)"
    # The calendar script returns no events when Outlook is closed, which looks like a
    # successful read, so check that Outlook is running first.
    running = run(["/usr/bin/pgrep", "-x", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if running.returncode != 0:
        return Check(name, False, f"{app_name} is not running", "Open Outlook for Mac, then run doctor again.", required=False)
    calendar_check = calendar_detector.current_event_titles()
    if calendar_check.checked:
        titles = ", ".join(calendar_check.event_titles) or "no Lunch event right now"
        return Check(name, True, f"readable ({titles})", required=False)
    return Check(
        name,
        False,
        f"could not read Outlook ({calendar_check.detail})",
        "Open Outlook for Mac. If macOS asks to let this app control Outlook, click OK.",
        required=False,
    )


def _check_launch_agent(run: Callable[..., subprocess.CompletedProcess]) -> Check:
    name = "Background watcher"
    install_fix = INSTALL_FIX
    if LEGACY_PLIST_PATH.exists():
        return Check(name, False, "an older copy is installed", install_fix)
    if not PLIST_PATH.exists():
        return Check(name, False, "not installed", install_fix)
    result = run(
        ["/bin/launchctl", "print", f"{_gui_target()}/{LABEL}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return Check(name, False, "installed but not loaded", install_fix)
    if "state = running" in result.stdout:
        return Check(name, True, "installed and running")
    return Check(name, False, f"installed but not running; see {ERROR_LOG_PATH}", install_fix)
