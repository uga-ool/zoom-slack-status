from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from .config import Config, config_from_env, load_env_file, validate_config
from .launch_agent import install as install_launch_agent
from .launch_agent import plist_xml, uninstall as uninstall_launch_agent
from .oauth import DEFAULT_REDIRECT_URI, build_authorize_url, code_challenge
from .oauth import exchange_code_for_token, generate_code_verifier, open_authorize_url
from .oauth import save_oauth_env, wait_for_local_callback
from .outlook import is_outlook_calendar_status
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
        slack = SlackClient(config.slack_user_token, dry_run=config.dry_run)
        status = slack.get_status()
        print(f"Slack: {status.emoji} {status.text}".strip())
        print(f"Slack expiration: {status.expiration}")
        if config.outlook_precedence:
            outlook_owned = is_outlook_calendar_status(status, config)
            print(f"Outlook Calendar precedence: {'active' if outlook_owned else 'not active'}")
    else:
        print("Slack: not checked because SLACK_USER_TOKEN is not set")
    return 0


def command_daemon(args: argparse.Namespace) -> int:
    config = _config(args, interval=args.interval)
    validate_config(config)
    detector = ZoomDetector()
    synchronizer = _synchronizer(config)

    if args.once:
        zoom_state = detector.detect()
        logging.info(
            "Zoom state: %s via %s (%s)",
            "busy" if zoom_state.in_meeting else "idle",
            zoom_state.signal,
            zoom_state.detail,
        )
        synchronizer.handle_zoom_state(zoom_state.in_meeting)
        return 0

    run_daemon(config, detector, synchronizer)
    return 0


def command_set_busy(args: argparse.Namespace) -> int:
    config = _config(args)
    validate_config(config)
    _synchronizer(config).force_busy()
    return 0


def command_restore(args: argparse.Namespace) -> int:
    config = _config(args)
    validate_config(config)
    _synchronizer(config).restore()
    return 0


def command_oauth_login(args: argparse.Namespace) -> int:
    client_id = (args.client_id or os.environ.get("SLACK_CLIENT_ID") or "").strip()
    if not client_id:
        raise ValueError("SLACK_CLIENT_ID is required. Add it to .env or pass --client-id.")

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
        print("Warning: Slack returned a rotating token. Disable token rotation for now, or rerun oauth login when this token expires.")
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


def run_daemon(config: Config, detector: ZoomDetector, synchronizer: StatusSynchronizer) -> None:
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

        if stable_state is not None:
            try:
                synchronizer.handle_zoom_state(stable_state)
            except SlackRateLimitError as exc:
                sleep_for = exc.retry_after or config.poll_seconds
                logging.warning("Slack rate limited the request; sleeping for %s seconds.", sleep_for)
                time.sleep(sleep_for)
            except SlackApiError as exc:
                sleep_for = max(60, config.poll_seconds)
                logging.error("Slack update failed: %s. Sleeping for %s seconds.", exc, sleep_for)
                time.sleep(sleep_for)

        time.sleep(config.poll_seconds)


def _config(args: argparse.Namespace, *, interval: int | None = None) -> Config:
    return config_from_env(dry_run=args.dry_run, interval=interval)


def _synchronizer(config: Config) -> StatusSynchronizer:
    slack = SlackClient(config.slack_user_token, dry_run=config.dry_run)
    store = StateStore(config.state_file)
    return StatusSynchronizer(config, slack, store)


if __name__ == "__main__":
    raise SystemExit(main())
