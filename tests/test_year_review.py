"""Fictional agent reports and legacy decision history migration."""
import importlib
import json
import unittest

from tests.test_sample_chat import package

module = importlib.import_module(f"{package.__name__}.year_review")
history = importlib.import_module(f"{package.__name__}.decision_history")


class YearReviewTests(unittest.TestCase):
    def test_fictional_year_is_stateless_and_cites_coverage(self):
        result = json.loads(module.explain_year({"year": 2026}))
        self.assertEqual(result["recordCount"], 4)
        self.assertEqual(result["mode"], "fictional")
        self.assertTrue(result["transferReview"]["items"])
        self.assertTrue(all(item["status"] == "pending" and item["evidence"]
                            for item in result["transferReview"]["items"]))
        self.assertTrue(any(flow["recordedFees"] != "0" for flow in result["flows"]))

    def test_year_scope_excludes_unrelated_rows_and_bad_input(self):
        empty = json.loads(module.explain_year({"year": 2025}))
        self.assertEqual(empty["recordCount"], 0)
        self.assertFalse(empty["transferReview"]["savedFictionalDecisionsAvailable"])
        scoped = json.loads(module.explain_year({"year": 2026, "source": "ethereum"}))
        self.assertTrue(all(flow["source"] == "ethereum" for flow in scoped["flows"]))
        self.assertEqual(scoped["transferReview"]["items"], [])
        self.assertIn("error", json.loads(module.explain_year({"year": True})))

    def test_legacy_latest_migrates_without_invented_history(self):
        accepted = {"status": "accepted", "revision": 5, "updatedAt": "earlier", "sourceRecords": ["private"]}
        undone = history.append_event(accepted, {"status": "undone", "revision": 6, "updatedAt": "later"})
        trail, incomplete = history.trail(undone)
        self.assertEqual([event["status"] for event in trail], ["accepted", "undone"])
        self.assertEqual([event["revision"] for event in trail], [5, 6])
        self.assertTrue(incomplete)
        self.assertNotIn("private", str(trail))

    def test_accountant_summary_uses_shared_year_flows_and_cites_explicit_links(self):
        fields = ("id", "at", "sourceMode", "kind", "asset", "quantity", "fee", "fee_asset", "evidence")
        rows = [{key: row[key] for key in fields} for row in module.fixture_rows()]
        cases, *_ = module.suggestions(module.fixture_rows())
        case = cases[0]
        link = {"status": "accepted", "outgoing": case["outgoing"], "incoming": case["incoming"]}
        report = module.accountant_years({"rows": rows, "links": [link]})
        expected = module.recorded_flows(rows, {case["outgoing"], case["incoming"]})
        self.assertEqual(report["years"][0]["flows"], expected)
        self.assertTrue(report["transfers"][0]["excludedFromFlows"])
        self.assertEqual(len(report["transfers"][0]["evidence"]), 2)
        self.assertTrue(any(row["recordedFees"] != "0" for row in expected))
        for invalid in ([link, link], [{**link, "outgoing": "missing"}]):
            with self.assertRaises(ValueError):
                module.accountant_years({"rows": rows, "links": invalid})

    def test_year_packet_does_not_remove_cross_year_legs_and_retains_signed_fees(self):
        fields = ("id", "at", "sourceMode", "kind", "asset", "quantity", "fee", "fee_asset", "evidence")
        rows = [{key: row[key] for key in fields} for row in module.fixture_rows()[:2]]
        rows[1]["at"] = "2027-01-01T00:00:00Z"
        rows[1]["fee"], rows[1]["fee_asset"] = "-0.000000001", rows[1]["asset"]
        link = {"status": "accepted", "outgoing": rows[0]["id"], "incoming": rows[1]["id"]}
        report = module.accountant_years({"rows": rows, "links": [link]})
        self.assertEqual([row["year"] for row in report["years"]], ["2026", "2027"])
        self.assertFalse(report["transfers"][0]["excludedFromFlows"])
        self.assertEqual(report["years"][1]["flows"][0]["recordedFees"], "-0.000000001")
