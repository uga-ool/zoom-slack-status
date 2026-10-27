import errno
import io
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest import mock

from zoom_slack_status import __main__ as cli


def run_main(argv):
    """Run the CLI and return (exit code, stderr text)."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code
    return code, stderr.getvalue()


class OAuthLoginTest(unittest.TestCase):
    def test_example_client_id_is_refused_before_opening_slack(self):
        with mock.patch.object(cli, "open_authorize_url") as open_url:
            code, stderr = run_main(
                ["oauth", "login", "--env-file", "/nonexistent/.env", "--client-id", "1234567890.1234567890"]
            )

        self.assertEqual(code, 2)
        self.assertIn("still the example value", stderr)
        open_url.assert_not_called()


class FriendlyErrorTest(unittest.TestCase):
    def run_with_error(self, error):
        # command_status loads config first, so failing there stands in for any command failing.
        with mock.patch.object(cli, "config_from_env", side_effect=error):
            return run_main(["status", "--env-file", "/nonexistent/.env"])

    def test_oauth_timeout_explains_next_step(self):
        code, stderr = self.run_with_error(TimeoutError("Timed out waiting for Slack OAuth callback."))

        self.assertEqual(code, 1)
        self.assertIn("click Allow", stderr)

    def test_port_in_use_is_explained(self):
        code, stderr = self.run_with_error(OSError(errno.EADDRINUSE, "Address already in use"))

        self.assertEqual(code, 1)
        self.assertIn("Port 8765 is already in use", stderr)

    def test_launchctl_failure_is_explained(self):
        code, stderr = self.run_with_error(
            subprocess.CalledProcessError(5, "launchctl", stderr="Bootstrap failed: 5")
        )

        self.assertEqual(code, 1)
        self.assertIn("could not start the background watcher (Bootstrap failed: 5)", stderr)

    def test_other_os_errors_still_raise(self):
        with self.assertRaises(PermissionError):
            self.run_with_error(PermissionError(errno.EACCES, "Permission denied"))


if __name__ == "__main__":
    unittest.main()
