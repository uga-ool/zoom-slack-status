from __future__ import annotations

from .config import Config
from .slack import SlackStatus


def is_outlook_calendar_status(status: SlackStatus, config: Config) -> bool:
    if not config.outlook_precedence:
        return False
    text = status.text.strip().casefold()
    return text in config.outlook_status_texts
