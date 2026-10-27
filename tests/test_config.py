import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zoom_slack_status.config import config_from_env, load_env_file, read_env_file, validate_config


class EnvFileTest(unittest.TestCase):
    def test_read_env_file_handles_comments_export_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# comment\n"
                "\n"
                "export SLACK_CLIENT_ID=123.456\n"
                'ZOOM_SLACK_STATUS_TEXT="On a Zoom call"\n'
                "ZOOM_SLACK_STATUS_EMOJI=':video_camera:'\n"
                "not a setting\n"
                "ZOOM_SLACK_POLL_SECONDS=5\n"
                "ZOOM_SLACK_POLL_SECONDS=9\n",
                encoding="utf-8",
            )

            values = read_env_file(env_path)

        self.assertEqual(
            values,
            {
                "SLACK_CLIENT_ID": "123.456",
                "ZOOM_SLACK_STATUS_TEXT": "On a Zoom call",
                "ZOOM_SLACK_STATUS_EMOJI": ":video_camera:",
                "ZOOM_SLACK_POLL_SECONDS": "5",
            },
        )

    def test_missing_env_file_reads_as_empty(self):
        self.assertEqual(read_env_file(Path("/nonexistent/.env")), {})

    def test_real_environment_wins_over_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("ZOOM_SLACK_STATUS_TEXT=From file\nZOOM_SLACK_STATUS_EMOJI=:file:\n", encoding="utf-8")

            with mock.patch.dict(os.environ, {"ZOOM_SLACK_STATUS_TEXT": "From env"}, clear=True):
                load_env_file(str(env_path))
                config = config_from_env()

        self.assertEqual(config.status_text, "From env")
        self.assertEqual(config.status_emoji, ":file:")


@mock.patch.dict(os.environ, {}, clear=True)
class ConfigFromEnvTest(unittest.TestCase):
    def test_defaults(self):
        config = config_from_env()

        self.assertEqual(config.status_text, "On a Zoom call")
        self.assertEqual(config.status_emoji, ":video_camera:")
        self.assertEqual(config.poll_seconds, 5)
        self.assertTrue(config.outlook_precedence)
        self.assertEqual(config.slack_token_expires_at, 0)

    def test_token_fields(self):
        os.environ.update(
            SLACK_CLIENT_ID="123.456", SLACK_REFRESH_TOKEN="xoxe-1-a", SLACK_TOKEN_EXPIRES_AT="1700000000"
        )

        config = config_from_env()

        self.assertEqual(config.slack_client_id, "123.456")
        self.assertEqual(config.slack_refresh_token, "xoxe-1-a")
        self.assertEqual(config.slack_token_expires_at, 1700000000)

    def test_bad_integer_names_the_setting(self):
        os.environ["ZOOM_SLACK_POLL_SECONDS"] = "five"

        with self.assertRaisesRegex(ValueError, "ZOOM_SLACK_POLL_SECONDS must be an integer"):
            config_from_env()

    def test_bool_parsing(self):
        os.environ["ZOOM_SLACK_OUTLOOK_PRECEDENCE"] = "off"
        self.assertFalse(config_from_env().outlook_precedence)
        os.environ["ZOOM_SLACK_OUTLOOK_PRECEDENCE"] = "Yes"
        self.assertTrue(config_from_env().outlook_precedence)

    def test_interval_overrides_poll_seconds(self):
        os.environ["ZOOM_SLACK_POLL_SECONDS"] = "30"
        self.assertEqual(config_from_env(interval=2).poll_seconds, 2)

    def test_validate_requires_token_unless_dry_run(self):
        with self.assertRaisesRegex(ValueError, "SLACK_USER_TOKEN is required"):
            validate_config(config_from_env())
        validate_config(config_from_env(dry_run=True))

    def test_validate_rejects_long_status_and_fast_polling(self):
        os.environ["ZOOM_SLACK_STATUS_TEXT"] = "x" * 101
        with self.assertRaisesRegex(ValueError, "100 characters"):
            validate_config(config_from_env(dry_run=True))

        os.environ["ZOOM_SLACK_STATUS_TEXT"] = "ok"
        os.environ["ZOOM_SLACK_POLL_SECONDS"] = "0"
        with self.assertRaisesRegex(ValueError, "at least 1"):
            validate_config(config_from_env(dry_run=True))


if __name__ == "__main__":
    unittest.main()
