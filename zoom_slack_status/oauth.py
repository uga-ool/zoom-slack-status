from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .config import read_env_file
from .slack import API_BASE, SlackApiError


DEFAULT_REDIRECT_URI = "http://localhost:8765/slack/oauth/callback"
DEFAULT_USER_SCOPES = ("users.profile:read", "users.profile:write")
TOKEN_REFRESH_MARGIN_SECONDS = 600


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    token_type: str
    user_id: str
    team_id: str
    refresh_token: str = ""
    expires_in: int = 0

    @property
    def expires_at(self) -> int:
        if not self.expires_in:
            return 0
        return int(time.time()) + self.expires_in


@dataclass(frozen=True)
class CallbackResult:
    code: str = ""
    state: str = ""
    error: str = ""


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:128]


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_authorize_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    user_scopes: tuple[str, ...] = DEFAULT_USER_SCOPES,
) -> str:
    params = {
        "client_id": client_id,
        "user_scope": ",".join(user_scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(params)


def wait_for_local_callback(redirect_uri: str, *, timeout_seconds: int) -> CallbackResult:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("The automatic callback listener requires an http://localhost redirect URI.")
    if not parsed.port:
        raise ValueError("The redirect URI must include a port, such as http://localhost:8765/...")

    result: Optional[CallbackResult] = None
    expected_path = parsed.path or "/"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            nonlocal result
            request_url = urllib.parse.urlparse(self.path)
            if request_url.path != expected_path:
                self._send_page(404, "Not found", "This OAuth listener is waiting for Slack.")
                return

            query = urllib.parse.parse_qs(request_url.query)
            result = CallbackResult(
                code=_first(query, "code"),
                state=_first(query, "state"),
                error=_first(query, "error"),
            )
            if result.error:
                self._send_page(
                    400,
                    "Slack authorization was not completed",
                    f"Slack returned: {html.escape(result.error)}",
                )
                return

            self._send_page(
                200,
                "Slack connected",
                "You can close this tab and return to Terminal.",
            )

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send_page(self, status: int, title: str, body: str) -> None:
            page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{
      color: #1d1c1d;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 4rem auto;
      max-width: 38rem;
      padding: 0 1.5rem;
    }}
    h1 {{ font-size: 1.6rem; }}
    p {{ color: #454245; line-height: 1.5; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>{body}</p>
</body>
</html>
"""
            encoded = page.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = HTTPServer((parsed.hostname, parsed.port), Handler)
    server.timeout = 1
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and result is None:
            server.handle_request()
    finally:
        server.server_close()

    if result is None:
        raise TimeoutError("Timed out waiting for Slack OAuth callback.")
    return result


def exchange_code_for_token(
    *,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> OAuthToken:
    payload = {
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    return _token_from_response(_oauth_request(payload))


def refresh_access_token(*, client_id: str, refresh_token: str) -> OAuthToken:
    # PKCE apps are public clients, so Slack refreshes without a client secret.
    payload = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    return _token_from_response(_oauth_request(payload))


class TokenRefresher:
    """Keeps a rotating Slack user token fresh and writes each new token back to the env file."""

    def __init__(
        self,
        *,
        env_file: Path,
        client_id: str,
        refresh_token: str,
        expires_at: int,
        refresh: Callable[..., OAuthToken] = refresh_access_token,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.env_file = env_file
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self._refresh = refresh
        self.logger = logger or logging.getLogger(__name__)

    def needs_refresh(self, *, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.expires_at) and self.expires_at - now <= TOKEN_REFRESH_MARGIN_SECONDS

    def refresh(self) -> str:
        # Another process (doctor, or a second command) may already have refreshed this file.
        # Reusing our in-memory refresh token after that would fail, because it is single-use.
        saved_token = self._adopt_saved_token()
        if saved_token and not self.needs_refresh():
            return saved_token

        try:
            token = self._refresh(client_id=self.client_id, refresh_token=self.refresh_token)
        except SlackApiError as exc:
            raise SlackApiError(
                f"Could not refresh the Slack token ({exc}). Run oauth login again, then launch-agent install."
            ) from exc

        # Slack revokes the old refresh token shortly after use, so save the new one right away.
        values = {"SLACK_USER_TOKEN": token.access_token}
        if token.refresh_token:
            self.refresh_token = token.refresh_token
            values["SLACK_REFRESH_TOKEN"] = token.refresh_token
        self.expires_at = token.expires_at
        if self.expires_at:
            values["SLACK_TOKEN_EXPIRES_AT"] = str(self.expires_at)
        update_env_file(self.env_file, values)
        self.logger.info("Refreshed Slack token; it now expires at %s.", self.expires_at or "(unknown)")
        return token.access_token

    def _adopt_saved_token(self) -> str:
        saved = read_env_file(self.env_file)
        saved_refresh = saved.get("SLACK_REFRESH_TOKEN", "")
        if not saved_refresh or saved_refresh == self.refresh_token:
            return ""
        self.refresh_token = saved_refresh
        try:
            self.expires_at = int(saved.get("SLACK_TOKEN_EXPIRES_AT") or 0)
        except ValueError:
            self.expires_at = 0
        self.logger.info("Using the Slack token another process already refreshed.")
        return saved.get("SLACK_USER_TOKEN", "")


def save_oauth_env(
    env_file: Path,
    *,
    client_id: str,
    redirect_uri: str,
    token: OAuthToken,
) -> None:
    values = {
        "SLACK_CLIENT_ID": client_id,
        "SLACK_REDIRECT_URI": redirect_uri,
        "SLACK_USER_TOKEN": token.access_token,
        "SLACK_USER_ID": token.user_id,
        "SLACK_TEAM_ID": token.team_id,
        "SLACK_TOKEN_TYPE": token.token_type,
    }
    if token.refresh_token:
        values["SLACK_REFRESH_TOKEN"] = token.refresh_token
    if token.expires_in:
        values["SLACK_TOKEN_EXPIRES_AT"] = str(token.expires_at)

    update_env_file(env_file, values)


def update_env_file(env_file: Path, values: Dict[str, str]) -> None:
    env_file = env_file.expanduser()
    lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True) if env_file.exists() else []
    remaining = dict(values)
    updated: list[str] = []

    for line in lines:
        key = _env_line_key(line)
        if key in remaining:
            updated.append(f"{key}={_env_value(remaining.pop(key))}\n")
        else:
            updated.append(line)

    if updated and not updated[-1].endswith("\n"):
        updated[-1] += "\n"
    for key, value in remaining.items():
        updated.append(f"{key}={_env_value(value)}\n")

    env_file.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically so a crash mid-write cannot lose a single-use refresh token.
    temp_path = env_file.with_name(env_file.name + ".tmp")
    temp_path.write_text("".join(updated), encoding="utf-8")
    temp_path.chmod(0o600)
    temp_path.replace(env_file)


def open_authorize_url(url: str) -> bool:
    return bool(webbrowser.open(url, new=2, autoraise=True))


def _oauth_request(payload: Dict[str, str]) -> Dict[str, Any]:
    body = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        API_BASE + "oauth.v2.access",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SlackApiError(f"Slack OAuth HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise SlackApiError(f"Could not reach Slack OAuth API: {exc}") from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise SlackApiError(f"Slack OAuth returned invalid JSON: {response_body}") from exc

    if not parsed.get("ok"):
        error = parsed.get("error", "unknown_error")
        raise SlackApiError(f"Slack OAuth error: {error}")
    return parsed


def _token_from_response(response: Dict[str, Any]) -> OAuthToken:
    # The code exchange nests user tokens under authed_user; a refresh returns them at the top level.
    authed_user = response.get("authed_user") or {}
    access_token = authed_user.get("access_token") or response.get("access_token") or ""
    if not access_token:
        raise SlackApiError("Slack OAuth response did not include a user access token.")

    team = response.get("team") or {}
    return OAuthToken(
        access_token=str(access_token),
        token_type=str(authed_user.get("token_type") or response.get("token_type") or ""),
        user_id=str(authed_user.get("id") or response.get("user_id") or ""),
        team_id=str(team.get("id") or response.get("team_id") or ""),
        refresh_token=str(authed_user.get("refresh_token") or response.get("refresh_token") or ""),
        expires_in=int(authed_user.get("expires_in") or response.get("expires_in") or 0),
    )


def _first(query: Dict[str, list[str]], key: str) -> str:
    values = query.get(key) or [""]
    return values[0]


def _env_line_key(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped or stripped.startswith("#"):
        return ""
    key = stripped.split("=", 1)[0].strip()
    if not key.replace("_", "").isalnum() or not key[:1].isalpha():
        return ""
    return key


def _env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() or char in {'"', "'", "#"} for char in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
