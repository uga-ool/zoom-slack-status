import html
import json
import unittest
from pathlib import Path

from zoom_slack_status.oauth import DEFAULT_REDIRECT_URI, DEFAULT_USER_SCOPES


REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "slack-app-manifest.json"


class SlackAppManifestTest(unittest.TestCase):
    def setUp(self):
        self.text = MANIFEST_PATH.read_text(encoding="utf-8").rstrip("\n")
        self.manifest = json.loads(self.text)

    def test_matches_what_oauth_login_expects(self):
        oauth_config = self.manifest["oauth_config"]

        self.assertEqual(oauth_config["redirect_urls"], [DEFAULT_REDIRECT_URI])
        self.assertEqual(oauth_config["scopes"]["user"], list(DEFAULT_USER_SCOPES))
        self.assertNotIn("bot", oauth_config["scopes"])
        self.assertTrue(oauth_config["pkce_enabled"])
        self.assertTrue(self.manifest["settings"]["token_rotation_enabled"])

    def test_fits_slack_length_limits(self):
        info = self.manifest["display_information"]

        self.assertLessEqual(len(info["name"]), 35)
        self.assertLessEqual(len(info["description"]), 140)

    def test_docs_embed_the_current_manifest(self):
        # People paste the copy in the docs, so it must match the file exactly.
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        guide = (REPO_ROOT / "docs" / "setup-guide" / "index.html").read_text(encoding="utf-8")

        self.assertIn(f"```json\n{self.text}\n```", readme)
        self.assertIn(f"<pre>{html.escape(self.text, quote=False)}</pre>", guide)


if __name__ == "__main__":
    unittest.main()
