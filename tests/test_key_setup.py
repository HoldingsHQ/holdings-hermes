"""Holdings key setup guide: the copy-paste blocks stay in sync and keep the key out of argv. Fictional values only."""
import base64
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = json.loads((ROOT / "dashboard" / "support.json").read_text(encoding="utf-8"))
ARTICLE = next(item for item in GUIDE["articles"] if item["id"] == "holdings-key")
COMMANDS = {item["label"]: item["code"] for item in ARTICLE["commands"]}
DOC = (ROOT / "docs" / "HOLDINGS_KEY_SETUP.md").read_text(encoding="utf-8")


def helper(code):
    line = next(row for row in code.splitlines() if row.startswith("hermes config set secrets.command.command "))
    return re.fullmatch(r"hermes config set secrets\.command\.command '(.+)'", line).group(1)


class KeySetupTests(unittest.TestCase):
    def test_every_block_in_the_app_matches_the_doc(self):
        self.assertEqual(len(COMMANDS), 7)
        for code in COMMANDS.values():
            self.assertIn("```sh\n" + code + "\n```", DOC) if "\n" in code else self.assertIn(code, DOC)

    def test_helpers_are_fast_noninteractive_and_authoritative(self):
        for label in ("Mac step 2. Let Hermes read it at startup", "Linux step 2. Let Hermes read it at startup"):
            code = COMMANDS[label]
            self.assertIn("hermes config set secrets.command.override_existing true", code)
            self.assertIn("hermes config set secrets.command.helper_timeout_seconds 3", code)
            self.assertTrue(code.rstrip().endswith("hermes config set secrets.command.enabled true"))
            self.assertRegex(helper(code), r'^k=\$\(.+\) && printf "HOLDINGS_VAULT_KEY=%s\\n" "\$k"$')

    def test_creation_never_replaces_a_key_and_never_puts_it_in_arguments(self):
        for label in ("Mac step 1. Make the key in your login Keychain", "Linux step 1. Make the key in your keyring",
                      "Or keep the key in 1Password"):
            code = COMMANDS[label]
            self.assertIn("|| ", code)
            self.assertIn("openssl rand -base64 32", code)
            self.assertNotRegex(code, r'-w "\$\(|password=\$\(')
            self.assertNotIn("-U ", code)
        self.assertIn("| security -i >/dev/null", COMMANDS["Mac step 1. Make the key in your login Keychain"])
        self.assertIn("| secret-tool store", COMMANDS["Linux step 1. Make the key in your keyring"])
        self.assertIn("| op item create --vault Private -", COMMANDS["Or keep the key in 1Password"])

    def test_helper_output_is_one_dotenv_line_and_fails_closed(self):
        value = base64.b64encode(bytes(range(32))).decode()
        with tempfile.TemporaryDirectory() as folder:
            for name, body in (("security", f'#!/bin/sh\n[ "$FAKE_MISSING" = 1 ] && exit 44\nprintf "%s\\n" "{value}"\n'),
                               ("secret-tool", f'#!/bin/sh\n[ "$FAKE_MISSING" = 1 ] && exit 1\nprintf "%s" "{value}"\n')):
                path = Path(folder) / name
                path.write_text(body)
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            env = {"PATH": folder + os.pathsep + "/usr/bin:/bin"}
            for label in ("Mac step 2. Let Hermes read it at startup", "Linux step 2. Let Hermes read it at startup"):
                command = helper(COMMANDS[label])
                done = subprocess.run(["/bin/sh", "-c", command], env=env, capture_output=True, text=True, timeout=3)
                self.assertEqual((done.returncode, done.stdout), (0, f"HOLDINGS_VAULT_KEY={value}\n"))
                self.assertEqual(len(base64.b64decode(done.stdout.split("=", 1)[1].strip(), validate=True)), 32)
                missing = subprocess.run(["/bin/sh", "-c", command], env={**env, "FAKE_MISSING": "1"},
                                         capture_output=True, text=True, timeout=3)
                self.assertNotEqual(missing.returncode, 0)
                self.assertEqual(missing.stdout, "")

    def test_holdings_accepts_only_the_sources_the_guide_uses(self):
        source = (ROOT / "storage_proof.py").read_text()
        self.assertIn('{" (from Command helper)", " (from 1Password)"}', source)
        self.assertIn("Bitwarden", DOC)
        self.assertNotRegex(DOC + json.dumps(ARTICLE), r"Keychain\.framework|SecItemCopyMatching")


if __name__ == "__main__":
    unittest.main()


class GatewayRestartGuideTests(unittest.TestCase):
    """Mac QA run: the CLI check passed while the running gateway still had no key."""

    def test_guide_restarts_the_gateway_before_the_check(self):
        labels = [item["label"] for item in ARTICLE["commands"]]
        restart = labels.index("Restart the gateway so it reads the key")
        self.assertEqual(COMMANDS[labels[restart]], "hermes gateway restart --detach")
        self.assertEqual(labels[restart + 1], "Check that Hermes can read the key")
        self.assertNotIn("Quit Hermes and open it again", ARTICLE["answer"] + DOC)
        self.assertIn("Quitting Hermes Desktop is not enough", ARTICLE["answer"])
        self.assertIn("Quitting Hermes Desktop is not enough", DOC)
        self.assertLess(DOC.index("hermes gateway restart --detach"), DOC.index("hermes holdings-storage check"))

    def test_guide_explains_profile_and_hermes_home(self):
        self.assertIn("HERMES_HOME", ARTICLE["answer"])
        self.assertIn("**HERMES_HOME.**", DOC)
        self.assertIn("Check again", ARTICLE["answer"])
        self.assertIn("does not retry a failed run", DOC)
