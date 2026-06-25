from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


API_BASE = "https://slack.com/api/"


@dataclass(frozen=True)
class SlackStatus:
    text: str
    emoji: str
    expiration: int = 0

    @classmethod
    def from_profile(cls, profile: Dict[str, Any]) -> "SlackStatus":
        return cls(
            text=str(profile.get("status_text") or ""),
            emoji=str(profile.get("status_emoji") or ""),
            expiration=int(profile.get("status_expiration") or 0),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SlackStatus":
        return cls(
            text=str(data.get("text") or ""),
            emoji=str(data.get("emoji") or ""),
            expiration=int(data.get("expiration") or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "emoji": self.emoji,
            "expiration": self.expiration,
        }

    def to_profile(self) -> Dict[str, Any]:
        return {
            "status_text": self.text,
            "status_emoji": self.emoji,
            "status_expiration": self.expiration,
        }

    def same_label(self, other: "SlackStatus") -> bool:
        return self.text == other.text and self.emoji == other.emoji


class SlackApiError(RuntimeError):
    pass


class SlackRateLimitError(SlackApiError):
    def __init__(self, retry_after: Optional[int]) -> None:
        self.retry_after = retry_after
        message = "Slack API rate limit hit"
        if retry_after is not None:
            message += f"; retry after {retry_after} seconds"
        super().__init__(message)


class SlackClient:
    def __init__(
        self,
        token: str,
        *,
        dry_run: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.token = token
        self.dry_run = dry_run
        self.logger = logger or logging.getLogger(__name__)
        self._dry_run_status = SlackStatus("", "", 0)

    def get_status(self) -> SlackStatus:
        if self.dry_run:
            self.logger.info("dry-run: would call users.profile.get")
            return self._dry_run_status

        response = self._request("users.profile.get", http_method="GET")
        return SlackStatus.from_profile(response.get("profile", {}))

    def set_status(self, status: SlackStatus) -> None:
        if self.dry_run:
            self.logger.info(
                "dry-run: would set Slack status to %r %s expiring at %s",
                status.text,
                status.emoji,
                status.expiration,
            )
            self._dry_run_status = status
            return

        self._request(
            "users.profile.set",
            http_method="POST",
            payload={"profile": status.to_profile()},
        )

    def _request(
        self,
        method: str,
        *,
        http_method: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            API_BASE + method,
            data=data,
            headers=headers,
            method=http_method,
        )

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_header = exc.headers.get("Retry-After")
                retry_after = int(retry_header) if retry_header and retry_header.isdigit() else None
                raise SlackRateLimitError(retry_after) from exc
            error_body = exc.read().decode("utf-8", errors="replace")
            raise SlackApiError(f"Slack HTTP {exc.code}: {error_body}") from exc
        except urllib.error.URLError as exc:
            raise SlackApiError(f"Could not reach Slack API: {exc}") from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SlackApiError(f"Slack returned invalid JSON: {body}") from exc

        if not parsed.get("ok"):
            error = parsed.get("error", "unknown_error")
            raise SlackApiError(f"Slack API error from {method}: {error}")

        return parsed

