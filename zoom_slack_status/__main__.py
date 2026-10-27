from __future__ import annotations

import argparse
import errno
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import Config, config_from_env, load_env_file, validate_config
from .doctor import format_checks, run_checks
from .launch_agent import APP_ENV_PATH, install as install_launch_agent
from .launch_agent import plist_xml, uninstall as uninstall_launch_agent
from .oauth import DEFAULT_REDIRECT_URI, EXAMPLE_CLIENT_ID, TokenRefresher, build_authorize_url, code_challenge
from .oauth import exchange_code_for_token, generate_code_verifier, open_authorize_url
from .oauth import save_oauth_env, wait_for_local_callback
from .outlook import any_lunch_event, is_outlook_calendar_status
from .outlook_calendar import OutlookCalendarDetector
from .slack import SlackApiError, SlackClient, SlackRateLimitError
from .sync import StateStore, StatusSynchronizer
from .zoom import ZoomDetector


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_env_file(getattr(args, "env_file", None))
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        return args.func(args)
    except ValueError as exc:
        parser.exit(2, f"error: {exc}\n")
    except SlackApiError as exc:
        parser.exit(1, f"slack error: {exc}\n")
    except TimeoutError:
        parser.exit(1, "error: Timed out waiting for Slack. Run oauth login again, then click Allow in the browser.\n")
    except FileNotFoundError as exc:
        parser.exit(2, f"error: {exc}\n")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or str(exc)
        parser.exit(1, f"error: macOS could not start the background watcher ({detail}). Run launch-agent install again.\n")
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        parser.exit(
            1,
            "error: Port 8765 is already in use, probably by another Slack sign-in that is still waiting. "
            "Close it or wait a few minutes, then try again.\n",
        )
    except KeyboardInterrupt:
        print("Stopped.")
        return 130


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--env-file", default=".env", help="Path to env file. Defaults to .env.")
    parent.add_argument("--dry-run", action="store_true", help="Log Slack updates without making them.")
    parent.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    parser = argparse.ArgumentParser(
        prog="zoom-slack-status",
        description="Set Slack status automatically while on Zoom calls.",
    )
    subparsers = parser.add_subparsers(required=True)

    status = subparsers.add_parser("status", parents=[parent], help="Show Zoom and Slack status.")
    status.set_defaults(func=command_status)

    # doctor has its own --env-file so it checks the background helper's settings once installed.
    doctor = subparsers.add_parser("doctor", help="Check that everything is set up.")
    doctor.add_argument(
        "--env-file",
        default=str(APP_ENV_PATH) if APP_ENV_PATH.exists() else ".env",
        help="Path to env file. Defaults to the background helper's copy once installed.",
    )
    doctor.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    doctor.set_defaults(func=command_doctor, dry_run=False)

    daemon = subparsers.add_parser("daemon", parents=[parent], help="Run the polling daemon.")
    daemon.add_argument("--once", action="store_true", help="Run one poll and exit.")
    daemon.add_argument("--interval", type=int, help="Override polling interval in seconds.")
    daemon.set_defaults(func=command_daemon)

    set_busy = subparsers.add_parser("set-busy", parents=[parent], help="Set Slack Zoom status now.")
    set_busy.set_defaults(func=command_set_busy)

    restore = subparsers.add_parser("restore", parents=[parent], help="Restore status from saved state.")
    restore.set_defaults(func=command_restore)

    oauth = subparsers.add_parser("oauth", parents=[parent], help="Authorize Slack with OAuth.")
    oauth_subparsers = oauth.add_subparsers(required=True)
    oauth_login = oauth_subparsers.add_parser("login", parents=[parent], help="Connect this Mac to Slack.")
    oauth_login.add_argument("--client-id", help="Slack app client ID. Defaults to SLACK_CLIENT_ID.")
    oauth_login.add_argument(
        "--redirect-uri",
        help=f"Slack OAuth redirect URI. Defaults to SLACK_REDIRECT_URI or {DEFAULT_REDIRECT_URI}.",
    )
    oauth_login.add_argument("--timeout", type=int, default=300, help="Seconds to wait for Slack.")
    oauth_login.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening it.")
    oauth_login.set_defaults(func=command_oauth_login)

    launch_agent = subparsers.add_parser(
        "launch-agent",
        parents=[parent],
        help="Install, uninstall, or print the macOS LaunchAgent.",
    )
    launch_agent.add_argument("action", choices=["install", "uninstall", "print"])
    launch_agent.add_argument(
        "--repo-dir",
        default=".",
        help="Directory containing this package. Defaults to current directory.",
    )
    launch_agent.set_defaults(func=command_launch_agent)

    return parser


def command_status(args: argparse.Namespace) -> int:
    config = _config(args)
    detector = ZoomDetector()
    zoom_state = detector.detect()
    print(f"Zoom: {'in a meeting' if zoom_state.in_meeting else 'not in a meeting'}")
    print(f"Signal: {zoom_state.signal} ({zoom_state.detail})")

    if config.slack_user_token or config.dry_run:
        slack = _slack_client(config, args)
        status = slack.get_status()
        print(f"Slack: {status.emoji} {status.text}".strip())
        print(f"Slack expiration: {status.expiration}")
        if config.outlook_precedence:
            outlook_owned = is_outlook_calendar_status(status, config)
            print(f"Outlook Calendar precedence: {'active' if outlook_owned else 'not active'}")
    else:
        print("Slack: not checked because SLACK_USER_TOKEN is not set")

    if config.lunch_enabled:
        calendar_check = OutlookCalendarDetector(app_name=config.outlook_app_name, subject_filters=config.lunch_labels).current_event_titles()
        if not calendar_check.checked:
            print(f"Lunch detection: could not read Outlook calendar ({calendar_check.detail})")
        else:
            titles = ", ".join(calendar_check.event_titles) or "(no current events)"
            lunch_active = any_lunch_event(calendar_check.event_titles, config)
            print(f"Lunch detection: {'active' if lunch_active else 'not active'} (current events: {titles})")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    config = _config(args)
    slack = _slack_client(config, args) if config.slack_user_token else None
    checks = run_checks(config, Path(args.env_file), slack=slack)
    sys.stdout.write(format_checks(checks))
    return 0 if all(check.ok or not check.required for check in checks) else 1


def command_daemon(args: argparse.Namespace) -> int:
    config = _config(args, interval=args.interval)
    validate_config(config)
    detector = ZoomDetector()
    calendar_detector = OutlookCalendarDetector(app_name=config.outlook_app_name, subject_filters=config.lunch_labels)
    synchronizer = _synchronizer(config, args)

    if args.once:
        zoom_state = detector.detect()
        logging.info(
            "Zoom state: %s via %s (%s)",
            "busy" if zoom_state.in_meeting else "idle",
            zoom_state.signal,
            zoom_state.detail,
        )
        synchronizer.handle_zoom_state(zoom_state.in_meeting)
        _check_lunch(config, calendar_detector, synchronizer)
        return 0

    run_daemon(config, detector, calendar_detector, synchronizer)
    return 0


def command_set_busy(args: argparse.Namespace) -> int:
    config = _config(args)
    validate_config(config)
    _synchronizer(config, args).force_busy()
    return 0


def command_restore(args: argparse.Namespace) -> int:
    config = _config(args)
    validate_config(config)
    _synchronizer(config, args).restore()
    return 0


def command_oauth_login(args: argparse.Namespace) -> int:
    client_id = (args.client_id or os.environ.get("SLACK_CLIENT_ID") or "").strip()
    if not client_id:
        raise ValueError("SLACK_CLIENT_ID is required. Add it to .env or pass --client-id.")
    if client_id == EXAMPLE_CLIENT_ID:
        raise ValueError(
            "SLACK_CLIENT_ID is still the example value. Copy your app's Client ID from "
            "Basic Information > App Credentials on api.slack.com and pass it with --client-id."
        )

    redirect_uri = (
        args.redirect_uri or os.environ.get("SLACK_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    ).strip()
    verifier = generate_code_verifier()
    state = generate_code_verifier()
    authorize_url = build_authorize_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        challenge=code_challenge(verifier),
    )

    print("Opening Slack authorization in your browser.")
    print("If Slack asks for approval, approve the requested profile scopes.")
    if args.no_browser or not open_authorize_url(authorize_url):
        print("\nOpen this URL:")
        print(authorize_url)

    callback = wait_for_local_callback(redirect_uri, timeout_seconds=args.timeout)
    if callback.error:
        raise ValueError(f"Slack authorization failed: {callback.error}")
    if callback.state != state:
        raise ValueError("Slack OAuth state did not match; refusing to save token.")
    if not callback.code:
        raise ValueError("Slack did not return an OAuth code.")

    token = exchange_code_for_token(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code=callback.code,
        verifier=verifier,
    )
    save_oauth_env(
        Path(args.env_file),
        client_id=client_id,
        redirect_uri=redirect_uri,
        token=token,
    )
    print(f"Saved Slack user token for user {token.user_id or '(unknown)'} to {args.env_file}.")
    if token.refresh_token:
        print("Token rotation is on; the background helper will refresh the token automatically.")
    print("Run launch-agent install again to use this token in the background helper.")
    return 0


def command_launch_agent(args: argparse.Namespace) -> int:
    repo_dir = Path(args.repo_dir).expanduser().resolve()
    env_file = Path(args.env_file).expanduser()
    if not env_file.is_absolute():
        env_file = repo_dir / env_file

    if args.action == "print":
        sys.stdout.write(plist_xml(repo_dir, env_file))
        return 0
    if args.action == "install":
        path = install_launch_agent(repo_dir, env_file)
        print(f"Installed LaunchAgent at {path}")
        return 0

    path = uninstall_launch_agent()
    print(f"Removed LaunchAgent at {path}")
    return 0


def run_daemon(
    config: Config,
    detector: ZoomDetector,
    calendar_detector: OutlookCalendarDetector,
    synchronizer: StatusSynchronizer,
) -> None:
    logging.info("Starting Zoom to Slack watcher. Polling every %s seconds.", config.poll_seconds)
    stable_state: bool | None = None
    busy_count = 0
    idle_count = 0

    while True:
        zoom_state = detector.detect()
        if zoom_state.in_meeting:
            busy_count += 1
            idle_count = 0
        else:
            idle_count += 1
            busy_count = 0

        next_state = stable_state
        if zoom_state.in_meeting and busy_count >= config.busy_confirmations:
            next_state = True
        elif not zoom_state.in_meeting and idle_count >= config.idle_confirmations:
            next_state = False

        if next_state != stable_state:
            logging.info(
                "Zoom state changed to %s via %s (%s).",
                "busy" if next_state else "idle",
                zoom_state.signal,
                zoom_state.detail,
            )
            stable_state = next_state
        else:
            logging.debug(
                "Zoom state remains %s via %s (%s).",
                "busy" if zoom_state.in_meeting else "idle",
                zoom_state.signal,
                zoom_state.detail,
            )

        try:
            if stable_state is not None:
                synchronizer.handle_zoom_state(stable_state)
            _check_lunch(config, calendar_detector, synchronizer)
        except SlackRateLimitError as exc:
            sleep_for = exc.retry_after or config.poll_seconds
            logging.warning("Slack rate limited the request; sleeping for %s seconds.", sleep_for)
            time.sleep(sleep_for)
        except SlackApiError as exc:
            sleep_for = max(60, config.poll_seconds)
            logging.error("Slack update failed: %s. Sleeping for %s seconds.", exc, sleep_for)
            time.sleep(sleep_for)

        time.sleep(config.poll_seconds)


def _check_lunch(
    config: Config,
    calendar_detector: OutlookCalendarDetector,
    synchronizer: StatusSynchronizer,
) -> None:
    if not config.lunch_enabled:
        return

    calendar_check = calendar_detector.current_event_titles()
    if not calendar_check.checked:
        logging.debug("Could not read Outlook calendar: %s", calendar_check.detail)
        return

    lunch_active = any_lunch_event(calendar_check.event_titles, config)
    synchronizer.handle_lunch_state(lunch_active)


def _config(args: argparse.Namespace, *, interval: int | None = None) -> Config:
    # Token refresh happens inside SlackClient, so only the copy of .env that owns the
    # refresh token ever spends it. See _slack_client.
    return config_from_env(dry_run=args.dry_run, interval=interval)


def _slack_client(config: Config, args: argparse.Namespace) -> SlackClient:
    refresher = None
    env_file = Path(args.env_file)
    can_refresh = config.slack_refresh_token and config.slack_client_id and not config.dry_run
    if can_refresh and _owns_refresh_token(env_file):
        refresher = TokenRefresher(
            env_file=env_file,
            client_id=config.slack_client_id,
            refresh_token=config.slack_refresh_token,
            expires_at=config.slack_token_expires_at,
        )
    return SlackClient(config.slack_user_token, dry_run=config.dry_run, token_refresher=refresher)


def _owns_refresh_token(env_file: Path) -> bool:
    # Refresh tokens are single-use. Once the background helper is installed, only its copy
    # of .env may refresh; otherwise the two copies would invalidate each other's tokens.
    if not APP_ENV_PATH.exists():
        return True
    return env_file.expanduser().resolve() == APP_ENV_PATH.resolve()


def _synchronizer(config: Config, args: argparse.Namespace) -> StatusSynchronizer:
    store = StateStore(config.state_file)
    return StatusSynchronizer(config, _slack_client(config, args), store)


if __name__ == "__main__":
    raise SystemExit(main())
