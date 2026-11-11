"""Fictional transfer matching, ambiguity and persistent decision semantics."""
import copy
import importlib
import unittest
from unittest.mock import patch

from build_demo import transfer_examples
from tests.test_kraken_review import package

transfers = importlib.import_module(f"{package.__name__}.transfers")


def examples():
    return [{**row, "originalId": row["id"]} for row in transfer_examples()]


class TransferTests(unittest.TestCase):
    def test_hash_and_fee_are_preferred_and_nearby_only_is_uncertain(self):
        items, limited, skipped = transfers.suggestions(examples())
        self.assertEqual([item["confidence"] for item in items], ["Hash match", "Uncertain"])
        self.assertFalse(limited)
        self.assertEqual(skipped, 0)
        self.assertEqual(items[0]["amountDifference"], "0.010")
        self.assertIn("You sent 0.5 ETH, paid a 0.01 ETH fee, and received 0.49 ETH", items[0]["reasons"][1])
        self.assertIn("no shared transaction ID", items[1]["reasons"][0])

    def test_same_ticker_on_different_chain_and_contradictory_hashes_do_not_match(self):
        outgoing, incoming, *_ = examples()
        different_chain = copy.deepcopy(incoming)
        different_chain["transfer"]["asset"] = "eip155:137/slip44:60"
        other_hash = copy.deepcopy(incoming)
        other_hash["transfer"]["hash"] = "0x" + "b" * 64
        same_account = copy.deepcopy(incoming)
        same_account["transfer"]["account"] = outgoing["transfer"]["account"]
        unknown_identity = copy.deepcopy(incoming)
        unknown_identity.pop("transfer")
        for other in (different_chain, other_hash, same_account, unknown_identity):
            with self.subTest(other=other):
                self.assertEqual(transfers.suggestions([outgoing, other])[0], [])
        self.assertEqual(transfers.suggestions([outgoing, incoming, incoming])[0], [])

    def test_alternatives_are_uncertain_and_old_identity_survives_formatting_changes(self):
        outgoing, incoming, *_ = examples()
        alternative = copy.deepcopy(incoming)
        alternative["id"] = alternative["originalId"] = alternative["transfer"]["ref"] = "alternate"
        all_items, *_ = transfers.suggestions([outgoing, incoming, alternative])
        self.assertEqual(len(all_items), 2)
        self.assertTrue(all(item["confidence"] == "Uncertain" for item in all_items))
        first, *_ = transfers.suggestions([outgoing, incoming])
        changed = {**outgoing, "quantity": "-0.5", "fee": "0.01", "id": "new-ordinal"}
        second, *_ = transfers.suggestions([changed, incoming])
        self.assertEqual(first[0]["id"], second[0]["id"])

    def test_accept_reject_undo_and_profile_reload_keep_source_records(self):
        files = {}

        def storage(mode):
            def persist(value):
                files[mode] = copy.deepcopy(value)
            return copy.deepcopy(files.get(mode)), persist

        rows = examples()
        base = {"mode": "combined"}
        with patch.object(transfers, "protected_snapshot", side_effect=storage):
            fresh = transfers.review({**base, "action": "list", "rows": rows})["items"]
            accept = {**base, "action": "accept", "id": fresh[0]["id"], "rows": rows[:2], "revision": 0}
            self.assertEqual(transfers.review(accept)["status"], "ownership-required")
            self.assertEqual(transfers.review({**accept, "owned": True})["item"]["status"], "accepted")
            self.assertEqual(transfers.review({**accept, "owned": True})["status"], "changed")
            alternate = copy.deepcopy(rows[1])
            alternate["id"] = alternate["originalId"] = alternate["transfer"]["ref"] = "alt"
            other, *_ = transfers.suggestions([rows[0], alternate])
            self.assertEqual(transfers.review({**accept, "rows": [rows[0], alternate], "owned": True,
                                               "id": other[0]["id"]})["status"], "already-linked")
            reject = {**base, "action": "reject", "id": fresh[1]["id"], "rows": rows[2:], "revision": 0}
            self.assertEqual(transfers.review(reject)["item"]["status"], "rejected")
            reopened = transfers.review({**base, "action": "list", "rows": rows})["items"]
            self.assertEqual([item["status"] for item in reopened], ["accepted", "rejected"])
            self.assertEqual(transfers.review({**accept, "action": "undo", "revision": 1})["item"]["status"], "undone")
            self.assertEqual(transfers.review({**reject, "action": "undo", "revision": 1})["item"]["status"], "undone")
        self.assertEqual(files["transfer-decisions"]["decisions"][fresh[0]["id"]]["sourceRecords"][0]["observations"],
                         rows[0]["observations"])
        self.assertEqual(rows, examples())
