from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .config import Config
from .outlook import is_outlook_calendar_status
from .slack import SlackClient, SlackStatus


STATE_VERSION = 1


@dataclass
class SyncState:
    managed: bool = False
    suppressed_until_idle: bool = False
    previous_status: Optional[SlackStatus] = None
    managed_status: Optional[SlackStatus] = None
    last_set_at: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncState":
        if data.get("version") != STATE_VERSION:
            return cls()
        previous = data.get("previous_status")
        managed = data.get("managed_status")
        return cls(
            managed=bool(data.get("managed", False)),
            suppressed_until_idle=bool(data.get("suppressed_until_idle", False)),
            previous_status=SlackStatus.from_dict(previous) if isinstance(previous, dict) else None,
            managed_status=SlackStatus.from_dict(managed) if isinstance(managed, dict) else None,
            last_set_at=float(data.get("last_set_at") or 0.0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "managed": self.managed,
            "suppressed_until_idle": self.suppressed_until_idle,
            "previous_status": self.previous_status.to_dict() if self.previous_status else None,
            "managed_status": self.managed_status.to_dict() if self.managed_status else None,
            "last_set_at": self.last_set_at,
        }


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SyncState:
        if not self.path.exists():
            return SyncState()
        try:
            return SyncState.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return SyncState()

    def save(self, state: SyncState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class StatusSynchronizer:
    def __init__(
        self,
        config: Config,
        slack: SlackClient,
        store: StateStore,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.config = config
        self.slack = slack
        self.store = store
        self.logger = logger or logging.getLogger(__name__)

    def handle_zoom_state(self, in_meeting: bool, *, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        state = self.store.load()

        if in_meeting:
            if state.suppressed_until_idle:
                self.logger.debug("Zoom is active; Slack status management is suppressed until idle.")
                return
            if state.managed:
                self._refresh_if_needed(state, now)
                return
            self._enter_meeting(now)
            return

        if state.managed:
            self._leave_meeting(state, now)
            return

        if state.suppressed_until_idle:
            self.logger.info("Zoom is idle; clearing Slack status suppression.")
            self.store.clear()

    def force_busy(self, *, now: Optional[float] = None) -> None:
        self._enter_meeting(time.time() if now is None else now)

    def restore(self, *, now: Optional[float] = None) -> None:
        state = self.store.load()
        if not state.managed:
            self.logger.info("No managed Slack status to restore.")
            self.store.clear()
            return
        self._leave_meeting(state, time.time() if now is None else now)

    def _enter_meeting(self, now: float) -> None:
        current = self.slack.get_status()
        if is_outlook_calendar_status(current, self.config):
            self.store.save(SyncState(suppressed_until_idle=True))
            self.logger.info(
                "Outlook Calendar status %r takes precedence; skipping Zoom status.",
                current.text,
            )
            return

        managed = self._busy_status(now)
        self.slack.set_status(managed)
        self.store.save(
            SyncState(
                managed=True,
                previous_status=current,
                managed_status=managed,
                last_set_at=now,
            )
        )
        self.logger.info("Set Slack status to %r %s.", managed.text, managed.emoji)

    def _refresh_if_needed(self, state: SyncState, now: float) -> None:
        if not state.managed_status:
            self.logger.warning("State file is missing managed status; resetting state.")
            self.store.clear()
            return

        refresh_seconds = self.config.refresh_seconds
        expires_soon = state.managed_status.expiration and state.managed_status.expiration - now <= 600
        refresh_due = refresh_seconds and now - state.last_set_at >= refresh_seconds
        if not refresh_due and not expires_soon:
            return

        current = self.slack.get_status()
        if not current.same_label(state.managed_status):
            if is_outlook_calendar_status(current, self.config):
                self.logger.info(
                    "Outlook Calendar status %r takes precedence; leaving it alone until Zoom is idle.",
                    current.text,
                )
            else:
                self.logger.info(
                    "Slack status changed during the call; leaving it alone until Zoom is idle."
                )
            state.managed = False
            state.suppressed_until_idle = True
            self.store.save(state)
            return

        managed = self._busy_status(now)
        self.slack.set_status(managed)
        state.managed_status = managed
        state.last_set_at = now
        self.store.save(state)
        self.logger.info("Refreshed Slack Zoom status expiration.")

    def _leave_meeting(self, state: SyncState, now: float) -> None:
        if not state.managed_status:
            self.store.clear()
            return

        current = self.slack.get_status()
        if current.same_label(state.managed_status):
            previous = self._restorable_previous(state.previous_status, now)
            self.slack.set_status(previous)
            self.logger.info("Restored previous Slack status.")
        else:
            if is_outlook_calendar_status(current, self.config):
                self.logger.info(
                    "Outlook Calendar status %r is active; skipping restore.",
                    current.text,
                )
            else:
                self.logger.info("Slack status was changed manually; skipping restore.")

        self.store.clear()

    def _busy_status(self, now: float) -> SlackStatus:
        expiration = 0
        if self.config.status_ttl_seconds:
            expiration = int(now + self.config.status_ttl_seconds)
        return SlackStatus(
            text=self.config.status_text,
            emoji=self.config.status_emoji,
            expiration=expiration,
        )

    @staticmethod
    def _restorable_previous(previous: Optional[SlackStatus], now: float) -> SlackStatus:
        if previous is None:
            return SlackStatus("", "", 0)
        if previous.expiration and previous.expiration <= int(now):
            return SlackStatus("", "", 0)
        return previous

