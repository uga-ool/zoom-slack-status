import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status import launch_agent
from zoom_slack_status.config import read_env_file


REPO_ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def isolated_install_paths():
    """Point every path launch_agent writes to at a temp dir, and stub out launchctl."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        support = root / "Application Support" / "zoom-slack-status"
        agents = root / "LaunchAgents"
        with mock.patch.multiple(
            launch_agent,
            APP_SUPPORT_DIR=support,
            APP_RUNTIME_DIR=support / "app",
            APP_ENV_PATH=support / ".env",
            APP_PYCACHE_DIR=support / "pycache",
            PLIST_PATH=agents / f"{launch_agent.LABEL}.plist",
            LEGACY_PLIST_PATH=agents / f"{launch_agent.LEGACY_LABEL}.plist",
        ), mock.patch.object(launch_agent, "_launchctl") as launchctl:
            yield root, launchctl


class BuildPlistTest(unittest.TestCase):
    def test_plist_runs_daemon_with_staged_paths(self):
        plist = launch_agent.build_plist(Path("/tmp/runtime"), Path("/tmp/runtime/.env"))

        self.assertEqual(plist["Label"], "edu.uga.online.zoom-slack-status")
        self.assertEqual(plist["ProgramArguments"][1:4], ["-m", "zoom_slack_status", "daemon"])
        self.assertEqual(plist["ProgramArguments"][-1], str(Path("/tmp/runtime/.env").resolve()))
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["EnvironmentVariables"]["PYTHONPATH"], str(Path("/tmp/runtime").resolve()))
        self.assertNotIn("/private/tmp", plist["EnvironmentVariables"]["PYTHONPYCACHEPREFIX"])


class InstallTest(unittest.TestCase):
    def write_source_env(self, root, *, expires_at, user_id="U1"):
        source_env = root / "source.env"
        source_env.write_text(
            f"SLACK_USER_ID={user_id}\n"
            "SLACK_USER_TOKEN=xoxe.xoxp-source\n"
            "SLACK_REFRESH_TOKEN=xoxe-1-source\n"
            f"SLACK_TOKEN_EXPIRES_AT={expires_at}\n"
            "ZOOM_SLACK_STATUS_TEXT=New text\n",
            encoding="utf-8",
        )
        return source_env

    def write_staged_env(self, *, expires_at, user_id="U1"):
        launch_agent.APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
        launch_agent.APP_ENV_PATH.write_text(
            f"SLACK_USER_ID={user_id}\n"
            "SLACK_USER_TOKEN=xoxe.xoxp-staged\n"
            "SLACK_REFRESH_TOKEN=xoxe-1-staged\n"
            f"SLACK_TOKEN_EXPIRES_AT={expires_at}\n"
            "ZOOM_SLACK_STATUS_TEXT=Old text\n",
            encoding="utf-8",
        )

    def test_install_stages_package_env_and_plist(self):
        with isolated_install_paths() as (root, launchctl):
            source_env = self.write_source_env(root, expires_at=100)

            path = launch_agent.install(REPO_ROOT, source_env)

            self.assertTrue(path.exists())
            self.assertTrue((launch_agent.APP_RUNTIME_DIR / "zoom_slack_status" / "__main__.py").exists())
            self.assertFalse((launch_agent.APP_RUNTIME_DIR / "zoom_slack_status" / "__pycache__").exists())
            self.assertEqual(launch_agent.APP_ENV_PATH.stat().st_mode & 0o777, 0o600)
            self.assertEqual(read_env_file(launch_agent.APP_ENV_PATH)["ZOOM_SLACK_STATUS_TEXT"], "New text")
            commands = [call.args[0][0] for call in launchctl.call_args_list]
            self.assertIn("bootstrap", commands)

    def test_reinstall_keeps_token_the_daemon_refreshed(self):
        with isolated_install_paths() as (root, _launchctl):
            source_env = self.write_source_env(root, expires_at=100)
            self.write_staged_env(expires_at=900)

            launch_agent.install(REPO_ROOT, source_env)

            staged = read_env_file(launch_agent.APP_ENV_PATH)
            self.assertEqual(staged["SLACK_REFRESH_TOKEN"], "xoxe-1-staged")
            self.assertEqual(staged["SLACK_USER_TOKEN"], "xoxe.xoxp-staged")
            self.assertEqual(staged["SLACK_TOKEN_EXPIRES_AT"], "900")
            self.assertEqual(staged["ZOOM_SLACK_STATUS_TEXT"], "New text")

    def test_new_login_replaces_staged_token(self):
        with isolated_install_paths() as (root, _launchctl):
            source_env = self.write_source_env(root, expires_at=900)
            self.write_staged_env(expires_at=100)

            launch_agent.install(REPO_ROOT, source_env)

            self.assertEqual(read_env_file(launch_agent.APP_ENV_PATH)["SLACK_REFRESH_TOKEN"], "xoxe-1-source")

    def test_different_slack_user_replaces_staged_token(self):
        with isolated_install_paths() as (root, _launchctl):
            source_env = self.write_source_env(root, expires_at=100, user_id="U2")
            self.write_staged_env(expires_at=900, user_id="U1")

            launch_agent.install(REPO_ROOT, source_env)

            self.assertEqual(read_env_file(launch_agent.APP_ENV_PATH)["SLACK_REFRESH_TOKEN"], "xoxe-1-source")

    def test_install_and_uninstall_remove_legacy_agent(self):
        with isolated_install_paths() as (root, _launchctl):
            source_env = self.write_source_env(root, expires_at=100)
            launch_agent.LEGACY_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            launch_agent.LEGACY_PLIST_PATH.write_text("legacy", encoding="utf-8")

            launch_agent.install(REPO_ROOT, source_env)
            self.assertFalse(launch_agent.LEGACY_PLIST_PATH.exists())

            launch_agent.LEGACY_PLIST_PATH.write_text("legacy", encoding="utf-8")
            launch_agent.uninstall()
            self.assertFalse(launch_agent.LEGACY_PLIST_PATH.exists())
            self.assertFalse(launch_agent.PLIST_PATH.exists())


if __name__ == "__main__":
    unittest.main()
