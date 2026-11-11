"""A locked Alert watch says what is missing with a fixed code, never the key or helper text."""
import base64
import importlib
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_sample_chat import package

storage = importlib.import_module(f"{package.__name__}.storage_proof")
watch = importlib.import_module(f"{package.__name__}.alert_watch")

FICTIONAL_KEY = base64.b64encode(b"F" * 32).decode()
HELPER_TEXT = "fictional-helper --print-key"


def context(label, value=None, configured=False):
    home = Path(tempfile.mkdtemp(prefix="holdings-locked-"))
    stack = [patch("agent.secret_scope.secret_source_label", return_value=label),
             patch("agent.secret_scope.get_secret", return_value=value),
             patch("hermes_constants.get_hermes_home", return_value=home),
             patch.object(storage, "source_configured", return_value=configured),
             patch.object(storage, "in_plugin_host", return_value=False)]
    for item in stack:
        item.start()
    try:
        return storage.protected_context()
    finally:
        for item in reversed(stack):
            item.stop()
        home.rmdir()


class ReasonTests(unittest.TestCase):
    def reason(self, *args, **kwargs):
        with self.assertRaises(storage.ProtectedKeyUnavailable) as caught:
            context(*args, **kwargs)
        return caught.exception.reason

    def test_source_set_up_but_not_loaded_means_restart(self):
        self.assertEqual(self.reason("", configured=True), "restart-needed")
        self.assertEqual(self.reason(" (from .env)", FICTIONAL_KEY, configured=True), "restart-needed")

    def test_no_source_means_not_set_up(self):
        self.assertEqual(self.reason("", configured=False), "not-set-up")
        self.assertEqual(self.reason("", configured=None), "not-set-up")

    def test_bad_value_means_wrong_format(self):
        self.assertEqual(self.reason(" (from Command helper)", "not base64!"), "wrong-format")
        self.assertEqual(self.reason(" (from 1Password)", base64.b64encode(b"short").decode()), "wrong-format")

    def test_good_key_opens(self):
        home, key, scope = context(" (from Command helper)", FICTIONAL_KEY)
        self.assertEqual(len(key), 32)

    def test_plugin_host_status_has_no_reason(self):
        with patch.object(storage, "in_plugin_host", return_value=True):
            with self.assertRaises(storage.ProtectedReviewHostUnavailable) as caught:
                storage.protected_context()
        self.assertIsNone(caught.exception.reason)
        def host(*args, **kwargs):
            raise caught.exception
        result = watch.request({"action": "read"}, open_store=host, context=host)
        self.assertEqual(result, {"status": "protected-host-unavailable"})


class SourceConfiguredTests(unittest.TestCase):
    def test_reads_only_flags(self):
        read = lambda doc: (lambda path: doc)
        helper = {"secrets": {"command": {"enabled": True, "command": HELPER_TEXT}}}
        self.assertTrue(storage.source_configured("/h", read=read(helper)))
        self.assertTrue(storage.source_configured("/h", read=read({"secrets": {"onepassword": {"enabled": True}}})))
        self.assertFalse(storage.source_configured("/h", read=read({"secrets": {"command": {"enabled": False, "command": HELPER_TEXT}}})))
        self.assertFalse(storage.source_configured("/h", read=read({"secrets": {"command": {"enabled": True, "command": ""}}})))
        self.assertFalse(storage.source_configured("/h", read=read({"secrets": {"command": {"enabled": "yes", "command": HELPER_TEXT}}})))
        self.assertFalse(storage.source_configured("/h", read=read({})))
        self.assertFalse(storage.source_configured("/h", read=read(None)))

    def test_unreadable_config_is_unknown(self):
        def broken(path):
            raise OSError("unreadable")
        self.assertIsNone(storage.source_configured("/h", read=broken))

    def test_reads_the_profile_config_file(self):
        seen = []
        storage.source_configured("/profiles/demo", read=lambda path: seen.append(path) or {})
        self.assertEqual(seen, [Path("/profiles/demo/config.yaml")])

    def test_real_hermes_reader_sees_a_later_edit(self):
        """The gateway reads config.yaml again after a change, so the reason stays current."""
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / "config.yaml"
            config.write_text("model: fictional\n")
            self.assertFalse(storage.source_configured(home))
            config.write_text("secrets:\n  command:\n    enabled: true\n    command: " + HELPER_TEXT + "\n")
            self.assertTrue(storage.source_configured(home))


    def test_default_reader_uses_the_public_config_module(self):
        """Catalog rule 9: the default reader comes from hermes_cli.config, not a core internal."""
        import ast
        tree = ast.parse(Path(storage.__file__).read_text(encoding="utf-8"))
        modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
        self.assertIn("hermes_cli.config", modules)
        self.assertNotIn("hermes_cli.config_backend", modules)


class StatusPayloadTests(unittest.TestCase):
    def test_payload_carries_only_the_reason_code(self):
        for reason in ("restart-needed", "not-set-up", "wrong-format"):
            def locked(*args, **kwargs):
                raise storage.ProtectedKeyUnavailable("Protected source is unavailable", reason)
            result = watch.request({"action": "read"}, open_store=locked, context=locked)
            self.assertEqual(result, {"status": "key-source-unavailable", "reason": reason})
            self.assertNotIn(HELPER_TEXT, repr(result))
            self.assertNotIn(FICTIONAL_KEY, repr(result))

    def test_unknown_reason_is_dropped(self):
        def locked(*args, **kwargs):
            raise storage.ProtectedKeyUnavailable("x", "helper said " + HELPER_TEXT)
        self.assertEqual(watch.request({"action": "read"}, open_store=locked, context=locked),
                         {"status": "key-source-unavailable"})


class CheckHintTests(unittest.TestCase):
    def test_passed_check_points_to_the_gateway_restart(self):
        home = Path("/fictional/home")
        with patch.object(storage, "protected_context", return_value=(home, b"F" * 32, b"S" * 32)):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(storage.check_key(None), 0)
        text = out.getvalue()
        self.assertIn("PASSED", text)
        self.assertIn("hermes gateway restart --detach", text)
        self.assertNotIn(FICTIONAL_KEY, text)


if __name__ == "__main__":
    unittest.main()
