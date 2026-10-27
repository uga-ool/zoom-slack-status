from __future__ import annotations

from typing import Iterable

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


def is_lunch_event_title(title: str, config: Config) -> bool:
    if not config.lunch_enabled:
        return False

    raw = (title or "").strip()
    if not raw:
        return False

    return outlook_status_label(raw) in config.lunch_labels


def any_lunch_event(titles: Iterable[str], config: Config) -> bool:
    return any(is_lunch_event_title(title, config) for title in titles)
