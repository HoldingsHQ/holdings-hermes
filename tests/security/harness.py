"""Synthetic audit helpers; host stubs exist only during an individual test."""
import base64
from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NAME = "holdings_security_" + hashlib.sha256(os.fsencode(ROOT)).hexdigest()
if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py",
                                                submodule_search_locations=[str(ROOT)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = package
    spec.loader.exec_module(package)


def mod(name):
    return importlib.import_module(NAME + "." + name)


def api():
    return mod("dashboard.plugin_api")


def client(raise_exc=True):
    try:
        from fastapi import FastAPI
    except ModuleNotFoundError as exc:
        if exc.name != "fastapi":
            raise
        raise unittest.SkipTest("Route security checks require FastAPI; run them in the Hermes test runtime") from None
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api().router)
    # This tests input handling, not Hermes's outer authorization mount.
    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=raise_exc)


@contextmanager
def host(home, secrets):
    names = ("agent", "agent.secret_scope", "hermes_constants")
    absent = object()
    previous = {name: sys.modules.get(name, absent) for name in names}
    agent = ModuleType("agent")
    agent.__path__ = []
    scope = ModuleType("agent.secret_scope")
    scope.get_secret = secrets.get
    scope.secret_source_label = lambda name: " (from Command helper)"
    agent.secret_scope = scope
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: home
    sys.modules.update({"agent": agent, "agent.secret_scope": scope, "hermes_constants": constants})
    try:
        with patch.dict(os.environ, {"HERMES_HOME": str(home), "HERMES_PLUGIN_HOST_PROCESS": "0"}):
            yield
    finally:
        for name, value in previous.items():
            if value is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


class SecurityTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="holdings-security-")
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.secrets = {"HOLDINGS_VAULT_KEY": base64.b64encode(os.urandom(32)).decode()}
        context = host(self.home, self.secrets)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        # No real sockets even if an adapter unexpectedly escapes a fake opener.
        blocked = patch("socket.socket.connect", side_effect=AssertionError("Live network forbidden in security tests"))
        blocked.start()
        self.addCleanup(blocked.stop)


class FakeResp:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_opener(handler):
    class Opener:
        def open(self, request, timeout=None):
            return FakeResp(handler(request.full_url, request.data))
    return lambda *args, **kwargs: Opener()
