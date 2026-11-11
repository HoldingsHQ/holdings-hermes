"""Run with the Hermes interpreter that supplies cryptography."""
import os
import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from vault import VaultLocked, load, rotate, store
from tests.test_kraken_review import review


class VaultChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "snapshot"
        self.key = os.urandom(32)
        self.scope = b"fictional-profile"
        self.marker = b"fictional ledger and chat fixture"

    def test_ciphertext_permissions_nonce_and_backup(self):
        store(self.path, self.marker, key=self.key, scope=self.scope)
        original = self.path.read_bytes()
        self.assertNotIn(self.marker, original)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        backup = self.root / "backup"
        shutil.copyfile(self.path, backup)
        self.assertNotIn(self.marker, backup.read_bytes())
        self.assertEqual(load(backup, key=self.key, scope=self.scope), self.marker)
        store(self.path, self.marker, key=self.key, scope=self.scope)
        self.assertNotEqual(original, self.path.read_bytes())

    def test_missing_key_wrong_profile_tamper_and_plaintext_fail_closed(self):
        store(self.path, self.marker, key=self.key, scope=self.scope)
        for key, scope in ((None, self.scope), (os.urandom(32), self.scope), (self.key, b"other")):
            with self.assertRaises(VaultLocked):
                load(self.path, key=key, scope=scope)
        data = self.path.read_bytes()
        for corrupted in (self.marker, data[:-1] + bytes([data[-1] ^ 1])):
            self.path.write_bytes(corrupted)
            with self.assertRaises(VaultLocked):
                load(self.path, key=self.key, scope=self.scope)

    def test_failed_replace_keeps_old_ciphertext_and_leaves_no_temp(self):
        store(self.path, self.marker, key=self.key, scope=self.scope)
        original = self.path.read_bytes()

        def interrupted(temporary, target):
            self.assertEqual(target, self.path)
            self.assertNotIn(self.marker, temporary.read_bytes())
            raise OSError("synthetic write failure")

        with patch("vault.os.replace", side_effect=interrupted), self.assertRaises(OSError):
            store(self.path, self.marker, key=self.key, scope=self.scope)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [self.path])

    def test_rotation_replaces_current_key_but_does_not_revoke_old_backups(self):
        store(self.path, self.marker, key=self.key, scope=self.scope)
        old_backup = self.root / "old-backup"
        shutil.copyfile(self.path, old_backup)
        new_key = os.urandom(32)
        rotate(self.path, old_key=self.key, new_key=new_key, scope=self.scope)
        with self.assertRaises(VaultLocked):
            load(self.path, key=self.key, scope=self.scope)
        self.assertEqual(load(self.path, key=new_key, scope=self.scope), self.marker)
        self.assertEqual(load(old_backup, key=self.key, scope=self.scope), self.marker)
        self.assertNotIn(self.marker, self.path.read_bytes())

    def test_kraken_legacy_snapshot_is_migrated_only_after_mode_validation(self):
        snapshot = {"mode": "kraken-spot", "rows": []}
        data = json.dumps(snapshot).encode()
        mode_scope = hashlib.sha256(self.scope + b"kraken-spot").digest()
        mode_error = importlib.import_module(review.__package__ + ".vault").VaultLocked
        with patch.object(review, "snapshot_location", return_value=(self.path, self.key, self.scope)):
            store(self.path, b'{"mode":"bybit-spot"}', key=self.key, scope=self.scope)
            original = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Wrong protected snapshot type"):
                review.reopen()
            self.assertEqual(self.path.read_bytes(), original)

            store(self.path, data, key=self.key, scope=self.scope)
            self.assertEqual(review.reopen(), snapshot)
            self.assertEqual(load(self.path, key=self.key, scope=mode_scope), data)
            with self.assertRaises(VaultLocked):
                load(self.path, key=self.key, scope=self.scope)

            for wrong_scope in (b"different-profile", hashlib.sha256(self.scope + b"bybit-spot").digest()):
                store(self.path, data, key=self.key, scope=wrong_scope)
                original = self.path.read_bytes()
                with self.assertRaises(mode_error):
                    review.reopen()
                with self.assertRaises(mode_error):
                    review.persist(snapshot)
                self.assertEqual(self.path.read_bytes(), original)

            self.path.unlink()
            review.persist(snapshot)
            self.assertEqual(json.loads(load(self.path, key=self.key, scope=mode_scope)), snapshot)

    def test_dashboard_loader_never_overwrites_a_foreign_package_or_adapter(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("holdings_dashboard_probe", root / "dashboard" / "plugin_api.py")
        dashboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dashboard)
        alias = "holdings_dashboard_" + hashlib.sha256(os.fsencode(root)).hexdigest()
        foreign = ModuleType("unrelated_fixture")
        with patch.dict(sys.modules, {alias: foreign}):
            with self.assertRaises(ImportError):
                dashboard.load_adapter("kraken_review")
            self.assertIs(sys.modules[alias], foreign)
        with patch.dict(sys.modules, {alias + ".kraken_review": foreign}):
            with self.assertRaises(ImportError):
                dashboard.load_adapter("kraken_review")
            self.assertIs(sys.modules[alias + ".kraken_review"], foreign)
        with patch.dict(sys.modules):
            adapter = dashboard.load_adapter("kraken_review")
            self.assertEqual(Path(adapter.__spec__.origin), root / "kraken_review.py")
