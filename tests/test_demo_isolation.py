"""Bundled demo actions never open or update a profile's protected files."""
import hashlib
import importlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_sample_chat import package

def module(name):
    return importlib.import_module(package.__name__ + "." + name)

class DemoIsolationTests(unittest.TestCase):
    def test_fictional_reads_and_rejected_writes_leave_no_files_with_a_key(self):
        transfers = module("transfers")
        conflicts = module("conflicts")
        watch = module("alert_watch")
        briefing = module("briefing")
        storage = module("storage_proof")
        with TemporaryDirectory() as temp:
            home = Path(temp)
            key = os.urandom(32)
            scope = hashlib.sha256(os.fsencode(home)).digest()
            with patch.object(storage, "protected_context", return_value=(home, key, scope)) as custody:
                rows = transfers.fixture_rows()
                demo = {"mode": "combined-sample"}
                report = transfers.review({**demo, "action": "list", "rows": rows})
                self.assertTrue(report["sessionOnly"])
                self.assertTrue(report["items"])
                with self.assertRaises(ValueError):
                    transfers.review({**demo, "action": "accept", "rows": rows})
                self.assertEqual(conflicts.review({**demo, "action": "list", "cases": []})["items"], [])
                with self.assertRaises(ValueError):
                    conflicts.review({**demo, "action": "exclude"})
                fixture = watch.request({"action": "fixture"}, context=custody)
                self.assertEqual(fixture["status"], "ready")
                self.assertTrue(fixture["alerts"][0]["fictional"])
                self.assertEqual(watch.request({"action": "cron-sample"}, allow_cron=True, context=custody),
                                 {"status": "invalid"})
                self.assertTrue(briefing.preview(sample=True)["fictional"])
                custody.assert_not_called()
                self.assertEqual(list(home.iterdir()), [])
