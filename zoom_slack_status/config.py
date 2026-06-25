from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_STATE_FILE = "~/Library/Application Support/zoom-slack-status/state.json"
DEFAULT_OUTLOOK_STATUS_TEXTS = "In a meeting,Working remotely,Out of office"


@dataclass(frozen=True)
class Config:
    slack_user_token: str
    status_text: str
    status_emoji: str
    status_ttl_minutes: int
    refresh_minutes: int
    poll_seconds: int
    busy_confirmations: int
    idle_confirmations: int
    state_file: Path
    dry_run: bool
    outlook_precedence: bool
    outlook_status_texts: frozenset[str]

    @property
    def status_ttl_seconds(self) -> int:
        return max(0, self.status_ttl_minutes * 60)

    @property
    def refresh_seconds(self) -> int:
        return max(0, self.refresh_minutes * 60)


def load_env_file(path: Optional[str]) -> None:
    if not path:
        return

    env_path = Path(path).expanduser()
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key:
            os.environ.setdefault(key, value)


def config_from_env(*, dry_run: bool = False, interval: Optional[int] = None) -> Config:
    effective_dry_run = dry_run or _env_bool("ZOOM_SLACK_DRY_RUN", False)
    return Config(
        slack_user_token=os.environ.get("SLACK_USER_TOKEN", "").strip(),
        status_text=os.environ.get("ZOOM_SLACK_STATUS_TEXT", "On a Zoom call").strip(),
        status_emoji=os.environ.get("ZOOM_SLACK_STATUS_EMOJI", ":video_camera:").strip(),
        status_ttl_minutes=_env_int("ZOOM_SLACK_STATUS_TTL_MINUTES", 120),
        refresh_minutes=_env_int("ZOOM_SLACK_REFRESH_MINUTES", 30),
        poll_seconds=interval or _env_int("ZOOM_SLACK_POLL_SECONDS", 15),
        busy_confirmations=max(1, _env_int("ZOOM_SLACK_BUSY_CONFIRMATIONS", 1)),
        idle_confirmations=max(1, _env_int("ZOOM_SLACK_IDLE_CONFIRMATIONS", 2)),
        state_file=Path(
            os.environ.get("ZOOM_SLACK_STATE_FILE", DEFAULT_STATE_FILE)
        ).expanduser(),
        dry_run=effective_dry_run,
        outlook_precedence=_env_bool("ZOOM_SLACK_OUTLOOK_PRECEDENCE", True),
        outlook_status_texts=_parse_outlook_status_texts(
            os.environ.get("ZOOM_SLACK_OUTLOOK_STATUS_TEXTS", DEFAULT_OUTLOOK_STATUS_TEXTS)
        ),
    )


def validate_config(config: Config) -> None:
    if not config.dry_run and not config.slack_user_token:
        raise ValueError("SLACK_USER_TOKEN is required unless --dry-run is used.")
    if len(config.status_text) > 100:
        raise ValueError("ZOOM_SLACK_STATUS_TEXT must be 100 characters or fewer.")
    if config.poll_seconds < 1:
        raise ValueError("ZOOM_SLACK_POLL_SECONDS must be at least 1.")
    if config.refresh_seconds and config.refresh_seconds < 60:
        raise ValueError("ZOOM_SLACK_REFRESH_MINUTES must be 0 or at least 1.")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_outlook_status_texts(raw: str) -> frozenset[str]:
    texts = {part.strip().casefold() for part in raw.split(",") if part.strip()}
    if not texts:
        raise ValueError("ZOOM_SLACK_OUTLOOK_STATUS_TEXTS must include at least one label.")
    return frozenset(texts)

