"""Protected state lives in <HERMES_HOME>/plugin-data/holdings, with a one-time move from holdings-private."""
import hashlib
import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_kraken_review import package

storage = importlib.import_module(f"{package.__name__}.storage_proof")
watch = importlib.import_module(f"{package.__name__}.alert_watch")


def legacy_folder(home, files=("kraken-spot.enc",)):
    old = Path(home) / "holdings-private"
    old.mkdir(mode=0o700)
    for name in files:
        (old / name).write_bytes(b"fictional sealed bytes " + name.encode())
    return old


class DataFolderTests(unittest.TestCase):
    def test_new_folder_is_plugin_data_and_owner_only(self):
        with TemporaryDirectory() as home:
            folder = storage.private_folder(home)
            self.assertEqual(folder, Path(home) / "plugin-data" / "holdings")
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertFalse((Path(home) / "holdings-private").exists())

    def test_old_folder_moves_once_with_contents_and_mode(self):
        with TemporaryDirectory() as home:
            legacy_folder(home, ("kraken-spot.enc", "alerts-watch-v1.enc"))
            folder = storage.private_folder(home)
            self.assertFalse((Path(home) / "holdings-private").exists())
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ["alerts-watch-v1.enc", "kraken-spot.enc"])
            self.assertEqual((folder / "kraken-spot.enc").read_bytes(), b"fictional sealed bytes kraken-spot.enc")
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            self.assertEqual(storage.private_folder(home), folder)

    def test_existing_new_folder_wins_and_old_folder_is_left_alone(self):
        with TemporaryDirectory() as home:
            old = legacy_folder(home)
            new = Path(home) / "plugin-data" / "holdings"
            new.mkdir(parents=True, mode=0o700)
            self.assertEqual(storage.private_folder(home), new)
            self.assertTrue((old / "kraken-spot.enc").is_file())
            self.assertEqual(list(new.iterdir()), [])

    def test_symlinked_old_folder_is_not_moved(self):
        with TemporaryDirectory() as home, TemporaryDirectory() as elsewhere:
            os.symlink(elsewhere, Path(home) / "holdings-private")
            folder = storage.private_folder(home)
            self.assertTrue((Path(home) / "holdings-private").is_symlink())
            self.assertFalse(folder.is_symlink())
            self.assertEqual(folder, Path(home) / "plugin-data" / "holdings")

    def test_status_check_moves_without_creating_a_new_folder(self):
        with TemporaryDirectory() as home:
            self.assertEqual(storage.private_folder(home, create=False), Path(home) / "plugin-data" / "holdings")
            self.assertFalse((Path(home) / "plugin-data").exists())
            legacy_folder(home, ("alerts-watch-v1.enc",))
            folder = storage.private_folder(home, create=False)
            self.assertTrue((folder / "alerts-watch-v1.enc").is_file())

    def test_failed_move_keeps_using_the_old_folder(self):
        with TemporaryDirectory() as home:
            old = legacy_folder(home)
            with patch.object(storage.os, "rename", side_effect=OSError("cross-device")):
                self.assertEqual(storage.private_folder(home), old)
            self.assertTrue((old / "kraken-spot.enc").is_file())

    def test_protected_snapshot_reads_moved_state(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography is required for the protected-storage integration")
        vault = importlib.import_module(f"{package.__name__}.vault")
        with TemporaryDirectory() as folder:
            home, key = Path(folder), os.urandom(32)
            scope = hashlib.sha256(os.fsencode(home)).digest()
            old = legacy_folder(home, ())
            vault.store(old / "alerts-watch-v1.enc", b'{"mode":"alerts-watch-v1","fictional":true}', key=key,
                        scope=hashlib.sha256(scope + b"alerts-watch-v1").digest())
            with patch.object(storage, "protected_context", return_value=(home, key, scope)):
                snapshot, _ = storage.protected_snapshot("alerts-watch-v1")
            self.assertEqual(snapshot, {"mode": "alerts-watch-v1", "fictional": True})
            self.assertTrue((home / "plugin-data" / "holdings" / "alerts-watch-v1.enc").is_file())
            self.assertFalse(old.exists())

    def test_alert_lock_uses_the_new_folder(self):
        with TemporaryDirectory() as home:
            legacy_folder(home)
            with watch.state_lock(home):
                pass
            self.assertTrue((Path(home) / "plugin-data" / "holdings" / ".alerts-watch.lock").is_file())
            self.assertTrue((Path(home) / "plugin-data" / "holdings" / "kraken-spot.enc").is_file())

    def test_path_matches_the_hermes_plugin_data_helper(self):
        try:
            from plugins.plugin_storage import plugin_data_dir
            from hermes_constants import get_hermes_home  # noqa: F401
        except ImportError:
            self.skipTest("Hermes source is not on the path")
        with TemporaryDirectory() as home, patch.dict(os.environ, {"HERMES_HOME": home}):
            folder = storage.private_folder(home)
            self.assertEqual(plugin_data_dir("holdings").resolve(), folder.resolve())
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
