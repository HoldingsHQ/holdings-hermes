"""Conflict decisions use exact fictional observations, independent of input order."""
import copy
import importlib
import unittest
from unittest.mock import patch

from build_demo import conflict_example
from tests.test_kraken_review import package

conflicts = importlib.import_module(f"{package.__name__}.conflicts")


class ConflictTests(unittest.TestCase):
    def test_preference_rescan_change_undo_and_scope(self):
        files = {}

        def storage(mode):
            def persist(value):
                files[mode] = copy.deepcopy(value)
            return copy.deepcopy(files.get(mode)), persist

        (identity, records), = conflict_example().items()
        case = {"mode": "kraken-spot", "originalId": identity, "observations": records}
        scope = {"mode": "combined"}
        listing = {**scope, "action": "list", "cases": [case]}
        with patch.object(conflicts, "protected_snapshot", side_effect=storage):
            item, = conflicts.review(listing)["items"]
            self.assertEqual(item["status"], "pending")
            prefer = {**scope, "action": "prefer", "case": case, "revision": 0,
                      "id": item["id"], "preferred": item["choices"][0]}
            self.assertEqual(conflicts.review(prefer)["item"]["status"], "preferred")
            self.assertEqual(conflicts.review(prefer)["status"], "changed")
            reopened, = conflicts.review({**listing, "cases": [{**case, "observations": records[::-1]}]})["items"]
            self.assertEqual((reopened["id"], reopened["preferred"]),
                             (item["id"], item["choices"][0]))
            changed = [dict(records[0], fee="0.0009"), records[1]]
            new, = conflicts.review({**listing, "cases": [{**case, "observations": changed}]})["items"]
            self.assertEqual(new["status"], "pending")
            self.assertEqual(conflicts.review({**listing, "mode": "combined-sample"})["items"][0]["status"], "pending")
            self.assertEqual(conflicts.review({**prefer, "action": "undo", "revision": 1})["item"]["status"], "undone")
            self.assertEqual(conflicts.review({**prefer, "action": "exclude", "revision": 2})["item"]["status"], "excluded")
        saved = files["conflict-decisions"]["decisions"][item["id"]]
        self.assertEqual(list(saved["sourceRecords"].values()), records)
        self.assertEqual(case["observations"], conflict_example()[identity])
