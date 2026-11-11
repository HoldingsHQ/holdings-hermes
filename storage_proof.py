"""Fictional storage acceptance check, with key custody outside Holdings."""
import base64
import hashlib
import json
import os
from pathlib import Path

KEY_NAME = "HOLDINGS_VAULT_KEY"
KEY_REASONS = frozenset({"not-set-up", "restart-needed", "wrong-format"})
FIXTURE_PREFIX = b"Holdings fictional storage proof "


DATA_FOLDER = ("plugin-data", "holdings")
LEGACY_FOLDER = "holdings-private"


def _documented_data_dir(home):
    """Hermes's plugin_data_dir helper, when it serves this same home."""
    try:
        from hermes_constants import get_hermes_home
        from plugins.plugin_storage import plugin_data_dir
    except ImportError:
        return None
    return plugin_data_dir if get_hermes_home().resolve() == Path(home).resolve() else None


def private_folder(home, *, create=True):
    """<HERMES_HOME>/plugin-data/holdings, created owner-only. Moves the old holdings-private folder once."""
    home = Path(home)
    folder = home.joinpath(*DATA_FOLDER)
    legacy = home / LEGACY_FOLDER
    if not os.path.lexists(folder) and legacy.is_dir() and not legacy.is_symlink():
        try:
            folder.parent.mkdir(exist_ok=True)
            os.rename(legacy, folder)
        except OSError:
            if not os.path.lexists(folder):
                return legacy
    if create:
        folder.parent.mkdir(exist_ok=True)
        folder.mkdir(mode=0o700, exist_ok=True)
        documented = _documented_data_dir(home)
        if documented is not None and documented(DATA_FOLDER[1]).resolve() != folder.resolve():
            raise ValueError("Protected storage path is not private")
    return folder


class ProtectedKeyUnavailable(ValueError):
    """The active Hermes context has no supported external key."""

    status = "key-source-unavailable"

    def __init__(self, message="Protected source is unavailable", reason="not-set-up"):
        super().__init__(message)
        # A fixed code for the Desktop status line. Never a key, path, helper text or provider error.
        self.reason = reason


class ProtectedReviewHostUnavailable(ProtectedKeyUnavailable):
    """The separate Hermes plugin host does not support protected reviews."""

    status = "protected-host-unavailable"


def in_plugin_host():
    # Hermes sets this non-credential marker in its separate plugin process.
    return os.getenv("HERMES_PLUGIN_HOST_PROCESS") == "1"


def source_configured(home, *, read=None):
    """True when this profile's config.yaml enables the command helper or 1Password source.

    Reads only the ``enabled`` flags and whether a helper command is present. The helper text,
    item references and any value stay unread here. None when the config cannot be read."""
    try:
        if read is None:
            # hermes_cli.config is a documented plugin surface. Same parser as the gateway.
            from hermes_cli.config import read_config_doc as read
        doc = read(Path(home) / "config.yaml") or {}
    except Exception:
        return None
    secrets = doc.get("secrets") if isinstance(doc, dict) else None
    if not isinstance(secrets, dict):
        return False
    command, onepassword = secrets.get("command"), secrets.get("onepassword")
    helper = isinstance(command, dict) and command.get("enabled") is True and bool(command.get("command"))
    vault = isinstance(onepassword, dict) and onepassword.get("enabled") is True
    return helper or vault


def protected_context():
    if in_plugin_host():
        raise ProtectedReviewHostUnavailable("Protected reviews are unavailable in Hermes plugin-host mode.", None)
    from agent.secret_scope import get_secret, secret_source_label
    from hermes_constants import get_hermes_home

    home = get_hermes_home().resolve()
    if secret_source_label(KEY_NAME) not in {" (from Command helper)", " (from 1Password)"}:
        # The profile names a key source but this running Hermes holds no key from it. Hermes runs
        # the helper when it starts and does not retry a failed run, so a restart is the next step.
        reason = "restart-needed" if source_configured(home) else "not-set-up"
        raise ProtectedKeyUnavailable("Protected source is unavailable", reason)
    try:
        key = base64.b64decode(get_secret(KEY_NAME) or "", validate=True)
    except (ValueError, TypeError):
        raise ProtectedKeyUnavailable("Protected key has the wrong format", "wrong-format") from None
    if len(key) != 32:
        raise ProtectedKeyUnavailable("Protected key has the wrong format", "wrong-format")
    return home, key, hashlib.sha256(os.fsencode(home)).digest()


def check_key(_args):
    try:
        from .vault import VaultLocked, seal, unseal
    except ImportError:
        print("BLOCKED: The isolated Hermes runtime needs cryptography.")
        return 2
    try:
        _, key, scope = protected_context()
        fixture = FIXTURE_PREFIX + os.urandom(24)
        if unseal(seal(fixture, key=key, scope=scope), key=key, scope=scope) != fixture:
            raise VaultLocked("Wrong snapshot type")
    except (OSError, RuntimeError, TypeError, ValueError):
        print("BLOCKED: Holdings could not use this profile's protected key.")
        return 2
    print("PASSED: The protected key authenticated an in-memory check.")
    print("This check starts a fresh Hermes process. If the Alerts page in Hermes Desktop still says "
          "Alert watch is locked, restart the gateway with: hermes gateway restart --detach")
    return 0


def register_storage(ctx):
    def setup(parser):
        parser.add_argument("action", choices=("check",))
    ctx.register_cli_command("holdings-storage", "Check the protected key in memory",
                             setup_fn=setup, handler_fn=check_key)


def protected_snapshot(mode):
    home, key, scope = protected_context()
    from .vault import load, store

    folder = private_folder(home)
    path = folder / f"{mode}.enc"
    if folder.is_symlink() or folder.stat().st_mode & 0o077 or path.is_symlink():
        raise ValueError("Protected storage path is not private")
    scope = hashlib.sha256(scope + mode.encode()).digest()
    try:
        snapshot = json.loads(load(path, key=key, scope=scope))
        if not isinstance(snapshot, dict) or snapshot.get("mode") != mode:
            raise ValueError("Wrong protected snapshot type")
    except FileNotFoundError:
        snapshot = None

    def persist(value):
        store(path, json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode(),
              key=key, scope=scope)

    return snapshot, persist
