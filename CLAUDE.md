# Zoom to Slack Status

A macOS background watcher that sets the user's Slack status while they are on a Zoom call and restores it afterward. It also sets a Lunch status from Outlook for Mac calendar events. Python 3.9+, standard library only, no dependencies.

## Setting this up for someone

If the user asks you to set this up, install it, or get it working, follow these steps in order. The user is often not technical: explain each step in plain words, tell them exactly what they will see, and do the Terminal work yourself.

Run every command from the repo root with `/usr/bin/python3`, not a Homebrew or virtualenv Python. The background watcher runs with whichever Python installed it, and the system Python is the one that is always there.

Expect to be asked to approve or run commands. That is normal; tell the user what each one does in a sentence.

1. **Check Python.** Run `/usr/bin/python3 --version`. If macOS offers to install the command line developer tools, tell the user to click **Install** and wait for it to finish (it can take several minutes), then run the command again.

2. **Slack app (the user does this).** Ask whether they already have a Slack app for this watcher. If they don't:
   - Run `pbcopy < slack-app-manifest.json` so the app settings are on their clipboard.
   - Walk them through the "Quick way: paste the app manifest" steps under **Step 1** in `README.md`. Where it says to paste, they press Command-V. Slack's button labels change from time to time, so describe each screen by what it does ("the button that creates the app") rather than insisting on exact words.
   - Signing in to Slack and creating the app happen in their own Slack account. Guide them, and let them do the clicking.
   - At the end, ask them to paste the **Client ID** from **Basic Information → App Credentials**. It looks like `1234567890.1234567890`. A Client ID is not a secret, so it is fine for them to paste it to you. Never ask for the Client Secret, the Signing Secret, or any token.

3. **Create `.env`.** Run `cp .env.example .env`. Do not edit it by hand: the next step writes the Client ID into it.

4. **Connect Slack.** Run `/usr/bin/python3 -m zoom_slack_status oauth login --env-file .env --client-id <their Client ID>`. It opens their browser and waits up to 5 minutes for them to click **Allow**. Run it with a Bash timeout of at least 330000 ms, or in the background. Tell them first: "Slack will open in your browser and ask for permission to see and change your status. Click Allow, then come back here." Never approve the Slack permission screen yourself.
   - If Slack says the app needs **approval** from a workspace admin (for example a "Request to Install" or "Submit" button), the user submits the request and waits for approval. The command will time out meanwhile. That is expected; run it again after the admin approves.
   - If it fails because port 8765 is in use, an earlier sign-in is still waiting. Wait for it to time out, then run it again.

5. **Install the background watcher.** Run `/usr/bin/python3 -m zoom_slack_status launch-agent install --env-file .env`. It copies the app and `.env` to `~/Library/Application Support/zoom-slack-status/` and starts it at login. Terminal does not need to stay open.

6. **Verify.** Run `/usr/bin/python3 -m zoom_slack_status doctor` and go through the results with them:
   - `[ok]` means that check passed.
   - `[!!]` is a problem. Follow the `Fix:` line under it, then run `doctor` again.
   - `[--]` is optional. "Accessibility" improves Zoom detection. "Outlook calendar for Lunch" only matters if they use Outlook for Mac.
   - If macOS asks whether something may control System Events or Outlook, the user clicks **OK**. Accessibility is different: macOS opens **System Settings → Privacy & Security → Accessibility**, and the user turns on the switch next to the app it names. Never change System Settings yourself.
   - `doctor` checks permissions for the app running `doctor`, which is you or Terminal. The background watcher asks for its own permission the first time it needs it, which can be a few minutes after install. Tell the user to watch for a second round of the same prompts, and to approve them too.

7. **Real test.** Ask them to join any Zoom meeting, even one with only themselves. Their Slack status should change to "On a Zoom call" within a few seconds and go back after they leave. If it doesn't, read `~/Library/Logs/zoom-slack-status.err.log`.

To update later: `git pull`, then run the `launch-agent install` command again. To remove it: `/usr/bin/python3 -m zoom_slack_status launch-agent uninstall`.

## Secrets

- `.env` holds the user's Slack token and refresh token. Never print, cat, summarize, or commit it, or the installed copy in `~/Library/Application Support/zoom-slack-status/`. To check which settings are present, print key names only, for example `grep -oE '^[A-Z_]+=' .env`.
- Redact anything that looks like `xoxp-`, `xoxe-`, or `xoxb-` before showing output to the user.
- After the watcher is installed, only its copy of `.env` refreshes the token. Slack refresh tokens can be used only once, so do not copy tokens between the two files by hand. If Slack stops working, run `oauth login` and `launch-agent install` again.

## Development

- Run the tests with `python3 -m unittest discover -s tests`. CI (`.github/workflows/test.yml`) runs them on macOS with Python 3.9 and 3.13.
- Keep the code standard-library only and compatible with Python 3.9, because that is the `/usr/bin/python3` that ships with macOS.
- Tests must not touch real paths: patch the paths in `launch_agent` and the `subprocess`/`urllib` calls, as the existing tests do.
- If you change the redirect URI or the scopes in `oauth.py`, update `slack-app-manifest.json` and the copies of it in `README.md` and `docs/setup-guide/index.html`. `tests/test_manifest.py` checks that they match.

Code map (`zoom_slack_status/`):
- `__main__.py`: the CLI and the polling loop (`run_daemon`).
- `zoom.py`: detects a meeting from the `CptHost` process, falling back to Zoom's menus via AppleScript.
- `sync.py`: the state machine that sets and restores the Zoom and Lunch statuses (state file v2).
- `slack.py`: the Slack Web API client; it refreshes tokens through `oauth.TokenRefresher`.
- `oauth.py`: PKCE login, token refresh, and `.env` writing.
- `outlook.py`, `outlook_calendar.py`: Outlook Calendar precedence and Lunch event detection.
- `launch_agent.py`: stages the runtime and installs the LaunchAgent `edu.uga.online.zoom-slack-status`.
- `doctor.py`: the setup checks behind `doctor`.
