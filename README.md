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

## Setup

**Setup guide (with screenshots):**

- [HTML guide](docs/setup-guide/index.html) — open in your browser (double-click the file after cloning, or use **File → Open** in Chrome/Safari)
- [Word document](docs/Zoom-Slack-Status-Setup-Guide.docx) — printable guide with the same steps

Each person creates **their own** Slack app and connects it on **their own** Mac. You do not share apps, Client IDs, or `.env` files with anyone else.

You will:

1. Create a personal Slack app and copy your **Client ID**
2. Download this project into **Documents**
3. Connect Slack with a one-time browser sign-in
4. Install the background watcher

Open **Terminal** (Applications → Utilities → Terminal). After you download the project, stay in `~/Documents/zoom-slack-status` for the Terminal steps. If Terminal says a file is missing, run `cd ~/Documents/zoom-slack-status` and try again.

Slack docs:

- Tokens and token types: [https://docs.slack.dev/authentication/tokens/](https://docs.slack.dev/authentication/tokens/)
- Installing with OAuth: [https://docs.slack.dev/authentication/installing-with-oauth/](https://docs.slack.dev/authentication/installing-with-oauth/)
- Using PKCE: [https://docs.slack.dev/authentication/using-pkce/](https://docs.slack.dev/authentication/using-pkce/)

### Step 1: Create your Slack app

Do this once per person. Use a desktop web browser (Chrome or Safari is fine).

1. Open [https://api.slack.com/apps](https://api.slack.com/apps) and sign in with your Slack account.
2. Click **Create New App**.
3. Choose **From scratch**.
4. Enter an app name you will recognize later, for example **Zoom Status Sync – Todd**.
5. Choose the Slack **workspace** where you want status updates (usually your work workspace).
6. Click **Create App**.

#### Redirect URL

7. In the left sidebar, click **OAuth & Permissions**.
8. Scroll to **Redirect URLs**.
9. Click **Add New Redirect URL**.
10. Paste this exactly:

```text
http://localhost:8765/slack/oauth/callback
```

11. Click **Add**, then click **Save URLs**.

#### PKCE

12. On the same **OAuth & Permissions** page, find **PKCE** and turn it **on**. This app runs on your Mac and does not use a client secret.

#### Scopes

13. Still on **OAuth & Permissions**, scroll to **Scopes**.
14. Under **User Token Scopes**, click **Add an OAuth Scope** and add:
    - `users.profile:read`
    - `users.profile:write`

Do not add Bot Token Scopes. This watcher only updates **your** profile status.

#### Token rotation (recommended)

15. On **OAuth & Permissions**, find **Advanced token security via token rotation**.
16. Click **Opt In**. Slack recommends this for security. See [token rotation](https://docs.slack.dev/authentication/using-token-rotation) in Slack's docs.

#### Copy your Client ID

17. In the left sidebar, click **Basic Information**.
18. Scroll to **App Credentials**.
19. Find **Client ID**. Click **Copy** (or select and copy the value). It looks like `1234567890.1234567890`.
20. Keep this somewhere handy for the next step — you will paste it into your `.env` file. **Do not share your Client ID or `.env` file with others.**

### Step 2: Download the project

In Terminal, run:

```sh
cd ~/Documents
git clone https://github.com/uga-ool/zoom-slack-status.git
cd zoom-slack-status
```

Your project folder is `~/Documents/zoom-slack-status` (Finder: **Documents → zoom-slack-status**).

If `git` is not installed, install Xcode Command Line Tools when macOS prompts you, then run the commands again.

### Step 3: Create and edit your `.env` file

1. Create your personal config file:

```sh
cp .env.example .env
```

2. Files whose names start with `.` (like `.env`) are hidden in Finder by default. To see them in **Documents → zoom-slack-status**, press **⌘ Command + Shift + .** (period). Press **⌘ Command + Shift + .** again when you want to hide them.

3. Open `.env` in TextEdit:

```sh
open -e .env
```

4. Find the line `SLACK_CLIENT_ID=` and paste **your** Client ID from Step 1 after the `=`:

```sh
SLACK_CLIENT_ID=1234567890.1234567890
SLACK_REDIRECT_URI=http://localhost:8765/slack/oauth/callback
```

Replace `1234567890.1234567890` with the value you copied from **Basic Information → App Credentials**. Leave `SLACK_REDIRECT_URI` as shown.

5. Save the file in TextEdit (**⌘ Command + S**) and close it.

Do not share `.env`. After setup it will also contain your personal Slack token.

### Step 4: Connect Slack

Run this from `~/Documents/zoom-slack-status`:

```sh
/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env
```

What happens:

1. Terminal prints a message and opens Slack in your browser.
2. Slack asks you to allow the app to view and update your profile status. Click **Allow**.
3. Your browser shows **Slack connected**. You can close that tab.
4. Return to Terminal. It should say your token was saved to `.env`.

If authorization fails, confirm your redirect URL, PKCE, and scopes from Step 1, then run the command again.

### Step 5: Install the background watcher

Still in `~/Documents/zoom-slack-status`:

```sh
/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env
```

This copies the helper to `~/Library/Application Support/zoom-slack-status/` and starts it in the background. You do not need to keep Terminal open after this.

To update later, open Terminal, run `cd ~/Documents/zoom-slack-status`, then `git pull`, then run the install command again. It stops the old watcher and replaces the staged copy automatically — no separate uninstall needed.

### Token rotation

When token rotation is enabled, Slack issues short-lived user tokens. This helper stores the access token in `.env` but does not automatically refresh it yet.

If status updates stop working, rerun OAuth login and reinstall the LaunchAgent:

```sh
cd ~/Documents/zoom-slack-status
/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env
/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env
```

OAuth login writes `SLACK_TOKEN_EXPIRES_AT` when Slack returns an expiration. If you see a warning about a rotating token during login, complete the steps above before the token expires.

### Manual token setup (optional)

You can skip OAuth login if you already have a user token. It must start with `xoxp-` and include `users.profile:read` and `users.profile:write`. Paste it into `.env` as `SLACK_USER_TOKEN`.

Do not use an app-level token from **Basic Information**. App-level tokens start with `xapp-` and cannot update your profile status.

## Try It

From `~/Documents/zoom-slack-status`, check what the watcher currently sees:

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

If you completed **Setup** above, the watcher is already installed. Use this section to reinstall, stop, or inspect it.

From `~/Documents/zoom-slack-status`, install a user LaunchAgent:

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
ZOOM_SLACK_POLL_SECONDS=5
ZOOM_SLACK_BUSY_CONFIRMATIONS=1
ZOOM_SLACK_IDLE_CONFIRMATIONS=1
ZOOM_SLACK_OUTLOOK_PRECEDENCE=true
ZOOM_SLACK_OUTLOOK_STATUS_TEXTS=In a meeting,Working remotely,Out of office
ZOOM_SLACK_STATE_FILE=~/Library/Application Support/zoom-slack-status/state.json
```

`ZOOM_SLACK_STATUS_TTL_MINUTES` is the safety-net expiration Slack receives. The daemon refreshes it while the meeting continues.

`ZOOM_SLACK_OUTLOOK_PRECEDENCE` defaults to `true`. Set it to `false` if you want Zoom status to overwrite Outlook Calendar statuses.

`ZOOM_SLACK_OUTLOOK_STATUS_TEXTS` is a comma-separated list of Slack status labels to treat as Outlook Calendar-owned. Matching is case-insensitive.

### Responsiveness

Status updates are not instant. The watcher polls Zoom on a timer, then updates Slack.

| Setting                         | Default | Effect                                          |
| ------------------------------- | ------- | ----------------------------------------------- |
| `ZOOM_SLACK_POLL_SECONDS`       | `5`     | How often Zoom is checked                       |
| `ZOOM_SLACK_BUSY_CONFIRMATIONS` | `1`     | Polls required before setting status on join    |
| `ZOOM_SLACK_IDLE_CONFIRMATIONS` | `1`     | Polls required before restoring status on leave |

With defaults, expect status to appear within about **0–5 seconds** of joining and clear within about **0–5 seconds** of leaving. Worst-case delay is roughly `poll_seconds × confirmations`.

Earlier defaults used a 15-second poll and two idle confirmations, which could take up to ~15 seconds to set status and ~30 seconds to clear. If status flickers when leaving calls, try `ZOOM_SLACK_IDLE_CONFIRMATIONS=2` (about 10 seconds to clear with a 5-second poll).

After changing `.env`, rerun `launch-agent install` so the background helper picks up the new values.

## macOS Permissions

The watcher first checks Zoom's meeting helper process. It also tries AppleScript UI inspection for extra signal. If macOS asks for Accessibility permission, allow your terminal app or the LaunchAgent runner to inspect Zoom. The process-based detector still works without that permission on many Zoom versions.
