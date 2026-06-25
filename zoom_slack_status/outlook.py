from __future__ import annotations

from .config import Config
from .slack import SlackStatus


def outlook_status_label(text: str) -> str:
    cleaned = text.strip()
    if "•" in cleaned:
        cleaned = cleaned.split("•", 1)[0].strip()
    elif " - " in cleaned:
        cleaned = cleaned.split(" - ", 1)[0].strip()
    return cleaned.casefold()


def is_outlook_calendar_status(status: SlackStatus, config: Config) -> bool:
    if not config.outlook_precedence:
        return False

    raw = status.text.strip()
    if not raw:
        return False

    if "outlook calendar" in raw.casefold():
        return True

    return outlook_status_label(raw) in config.outlook_status_texts
