from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict


LABEL = "com.local.zoom-slack-status"
PLIST_PATH = Path("~/Library/LaunchAgents/com.local.zoom-slack-status.plist").expanduser()
APP_SUPPORT_DIR = Path("~/Library/Application Support/zoom-slack-status").expanduser()
APP_RUNTIME_DIR = APP_SUPPORT_DIR / "app"
APP_ENV_PATH = APP_SUPPORT_DIR / ".env"


def build_plist(runtime_dir: Path, env_file: Path) -> Dict[str, object]:
    log_dir = Path("~/Library/Logs").expanduser()
    runtime_dir = runtime_dir.expanduser().resolve()
    return {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "zoom_slack_status",
            "daemon",
            "--env-file",
            str(env_file.expanduser().resolve()),
        ],
        "WorkingDirectory": str(runtime_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / "zoom-slack-status.log"),
        "StandardErrorPath": str(log_dir / "zoom-slack-status.err.log"),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(runtime_dir),
            "PYTHONPYCACHEPREFIX": "/private/tmp/zoom-slack-status-pycache",
            "PYTHONUNBUFFERED": "1",
        },
    }


def plist_xml(repo_dir: Path, env_file: Path) -> str:
    return plistlib.dumps(build_plist(repo_dir, env_file), sort_keys=False).decode("utf-8")


def install(repo_dir: Path, env_file: Path) -> Path:
    runtime_dir, installed_env_file = _stage_runtime(repo_dir, env_file)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(
        plistlib.dumps(build_plist(runtime_dir, installed_env_file), sort_keys=False)
    )
    _launchctl(["bootout", _gui_target(), str(PLIST_PATH)], check=False)
    _launchctl(["bootstrap", _gui_target(), str(PLIST_PATH)], check=True)
    _launchctl(["enable", f"{_gui_target()}/{LABEL}"], check=False)
    return PLIST_PATH


def uninstall() -> Path:
    _launchctl(["bootout", _gui_target(), str(PLIST_PATH)], check=False)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    return PLIST_PATH


def _gui_target() -> str:
    return f"gui/{os.getuid()}"


def _stage_runtime(repo_dir: Path, env_file: Path) -> tuple[Path, Path]:
    repo_dir = repo_dir.expanduser().resolve()
    env_file = env_file.expanduser().resolve()
    source_package = repo_dir / "zoom_slack_status"
    if not source_package.is_dir():
        raise FileNotFoundError(f"Could not find package directory: {source_package}")
    if not env_file.exists():
        raise FileNotFoundError(f"Could not find env file: {env_file}")

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    if APP_RUNTIME_DIR.exists():
        shutil.rmtree(APP_RUNTIME_DIR)
    shutil.copytree(source_package, APP_RUNTIME_DIR / "zoom_slack_status")
    shutil.copy2(env_file, APP_ENV_PATH)
    APP_ENV_PATH.chmod(0o600)
    return APP_RUNTIME_DIR, APP_ENV_PATH


def _launchctl(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
