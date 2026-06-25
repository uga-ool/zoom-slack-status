# Zoom to Slack Status

Automatically sets your Slack status while you are on a Zoom call, then restores your previous status when the call ends.

This is a local macOS watcher. It does not need a Zoom API key. It detects an active Zoom meeting from local Zoom processes and, when available, Zoom's macOS UI state. Slack is updated with the official `users.profile.get` and `users.profile.set` Web API methods.

Slack docs:

- `users.profile.get`: [https://docs.slack.dev/reference/methods/users.profile.get/](https://docs.slack.dev/reference/methods/users.profile.get/)
- `users.profile.set`: [https://docs.slack.dev/reference/methods/users.profile.set/](https://docs.slack.dev/reference/methods/users.profile.set/)

## What It Does

- Detects active Zoom meetings on macOS.
- Sets your Slack status to `:zoom: On a Zoom call`.
- Stores your previous Slack status before changing it.
- Restores that previous status when the Zoom meeting ends.
- Skips restore if you manually changed your Slack status during the call.
- Yields to Slack's Outlook Calendar app when it has already set your status.
- Uses a status expiration as a safety net, so the Zoom status will not stay forever if the watcher stops.

## Status Precedence

If you use [Outlook Calendar for Slack](https://slack.com/help/articles/360020134853-Microsoft-Outlook-Calendar-for-Slack), that app's auto status takes precedence over Zoom status sync.

When Outlook Calendar has already set your Slack status to one of its default labels (`In a meeting`, `Working remotely`, or `Out of office`), this watcher will not overwrite it when you join a Zoom call. If your Zoom status was already set and Outlook updates your status during the call, the watcher stops managing Zoom status and leaves Outlook's label in place. When Zoom goes idle, the watcher clears its internal state without changing Slack.

This detection is based on Slack status text labels, not on reading Outlook directly. Slack's Outlook Calendar app often appends `• Outlook Calendar` to the status text (for example, `In a meeting • Outlook Calendar`); this watcher treats those as Outlook-owned. Outlook Calendar will not override a status you set yourself, so if this watcher sets `On a Zoom call` first, Outlook cannot take over until the watcher yields (for example, when refresh detects a different status).

Disable precedence with `ZOOM_SLACK_OUTLOOK_PRECEDENCE=false`, or customize the Outlook labels with `ZOOM_SLACK_OUTLOOK_STATUS_TEXTS`.

## Requirements

- macOS
- Python 3.9 or newer
- A Slack user token with these user token scopes:
  - `users.profile:read`
  - `users.profile:write`

Slack's `users.profile.set` method requires a user token for status updates. Slack documents these profile status fields: `status_text`, `status_emoji`, and `status_expiration`.

Do not generate an app-level token from **Basic Information**. App-level tokens start with `xapp-` and Slack only allows them to access specific app-wide APIs. This watcher needs a user OAuth token, which starts with `xoxp-`.

## Slack OAuth Setup For Teams

For a team rollout, regular Slack members should not need access to Slack developer settings and should not share tokens. Configure the Slack app once, then each teammate authorizes their own user token locally.

Slack docs:

- Tokens and token types: [https://docs.slack.dev/authentication/tokens/](https://docs.slack.dev/authentication/tokens/)
- Installing with OAuth: [https://docs.slack.dev/authentication/installing-with-oauth/](https://docs.slack.dev/authentication/installing-with-oauth/)
- Using PKCE: [https://docs.slack.dev/authentication/using-pkce/](https://docs.slack.dev/authentication/using-pkce/)

### One-Time Slack App Setup

Do this once as the Slack app owner or admin. Create or open the shared Slack app (for example, **Zoom Status Sync**) at [https://api.slack.com/apps](https://api.slack.com/apps).

1. Open **OAuth & Permissions**.
2. Under **Redirect URLs**, add:

```text
http://localhost:8765/slack/oauth/callback
```

3. In **OAuth & Permissions**, enable **PKCE**. This lets desktop/local installs use OAuth without sharing a client secret.
4. Under **User Token Scopes**, add:
   - `users.profile:read`
   - `users.profile:write`
5. On **OAuth & Permissions**, opt in to **Advanced token security via token rotation**. Slack recommends this for security-minded teams. See [token rotation](https://docs.slack.dev/authentication/using-token-rotation) in Slack's docs.
6. Copy the app's **Client ID** from **Basic Information**. Share only this Client ID with teammates, not a client secret.

If your workspace requires app approval, a Workspace Owner or app manager may need to approve this app before regular members can authorize it.

### Token Rotation

When token rotation is enabled, Slack issues short-lived user tokens. This helper stores the access token in `.env` but does not automatically refresh it yet.

If status updates stop working, rerun OAuth login and reinstall the LaunchAgent:

```sh
/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env
/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env
```

OAuth login writes `SLACK_TOKEN_EXPIRES_AT` when Slack returns an expiration. If you see a warning about a rotating token during login, complete the steps above before the token expires.

### Teammate Setup

Each teammate should run this on their own Mac:

```sh
cp .env.example .env
```

Edit `.env` and set the shared Slack app Client ID:

```sh
SLACK_CLIENT_ID=1234567890.1234567890
SLACK_REDIRECT_URI=http://localhost:8765/slack/oauth/callback
```

Then connect Slack:

```sh
/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env
```

The command opens Slack in the browser, waits for the localhost callback, then writes that teammate's personal `SLACK_USER_TOKEN` into `.env`.

Install the background watcher:

```sh
/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env
```

Do not share `.env` files. They contain personal Slack user tokens.

### Manual Token Setup

You can still use a manually copied user token instead of OAuth onboarding. It must be a user OAuth token starting with `xoxp-` and it must include:

- `users.profile:read`
- `users.profile:write`

Do not generate an app-level token from **Basic Information**. App-level tokens start with `xapp-` and Slack only allows them to access specific app-wide APIs.

## Try It

Check what the watcher currently sees:

```sh
/usr/bin/python3 -m zoom_slack_status status --env-file .env
```

Run in dry-run mode:

```sh
/usr/bin/python3 -m zoom_slack_status daemon --env-file .env --dry-run
```

Run for real:

```sh
/usr/bin/python3 -m zoom_slack_status daemon --env-file .env
```

## Run In The Background

Install a user LaunchAgent:

```sh
/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env
```

The installer copies the runnable helper and `.env` file to `~/Library/Application Support/zoom-slack-status/` before starting the LaunchAgent. This avoids macOS background-access problems with files under `~/Documents`.

If you change `.env` later, run the install command again so the background helper receives the updated settings.

Stop and remove it:

```sh
/usr/bin/python3 -m zoom_slack_status launch-agent uninstall
```

Print the LaunchAgent plist without installing it:

```sh
/usr/bin/python3 -m zoom_slack_status launch-agent print --env-file .env
```

Logs are written to:

- `~/Library/Logs/zoom-slack-status.log`
- `~/Library/Logs/zoom-slack-status.err.log`

## Configuration

All settings are optional except `SLACK_USER_TOKEN` when not using `--dry-run`.

```sh
SLACK_CLIENT_ID=1234567890.1234567890
SLACK_REDIRECT_URI=http://localhost:8765/slack/oauth/callback
SLACK_USER_TOKEN=xoxp-your-user-token
ZOOM_SLACK_STATUS_TEXT=On a Zoom call
ZOOM_SLACK_STATUS_EMOJI=:video_camera:
ZOOM_SLACK_STATUS_TTL_MINUTES=120
ZOOM_SLACK_REFRESH_MINUTES=30
ZOOM_SLACK_POLL_SECONDS=15
ZOOM_SLACK_BUSY_CONFIRMATIONS=1
ZOOM_SLACK_IDLE_CONFIRMATIONS=2
ZOOM_SLACK_OUTLOOK_PRECEDENCE=true
ZOOM_SLACK_OUTLOOK_STATUS_TEXTS=In a meeting,Working remotely,Out of office
ZOOM_SLACK_STATE_FILE=~/Library/Application Support/zoom-slack-status/state.json
```

`ZOOM_SLACK_STATUS_TTL_MINUTES` is the safety-net expiration Slack receives. The daemon refreshes it while the meeting continues.

`ZOOM_SLACK_OUTLOOK_PRECEDENCE` defaults to `true`. Set it to `false` if you want Zoom status to overwrite Outlook Calendar statuses.

`ZOOM_SLACK_OUTLOOK_STATUS_TEXTS` is a comma-separated list of Slack status labels to treat as Outlook Calendar-owned. Matching is case-insensitive.

## macOS Permissions

The watcher first checks Zoom's meeting helper process. It also tries AppleScript UI inspection for extra signal. If macOS asks for Accessibility permission, allow your terminal app or the LaunchAgent runner to inspect Zoom. The process-based detector still works without that permission on many Zoom versions.
