"""Citations and scope boundaries for the companion toolset."""
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_sample_chat import package

companion = importlib.import_module(f"{package.__name__}.companion")


class CompanionTests(unittest.TestCase):
    def test_registry_and_fictional_reports(self):
        registry = {}
        companion.register_tools(SimpleNamespace(get_config=lambda *_: False,
            register_tool=lambda **spec: registry.update({spec["name"]: spec})))
        examples = {
            "holdings_summary": {}, "holdings_exposure": {"asset": "eth"},
            "holdings_recent_movements": {"since": "2026-01-01"}, "holdings_needs_attention": {},
            "holdings_explain_year": {"year": 2026},
        }
        self.assertEqual(set(registry), set(examples))
        for name, args in examples.items():
            with self.subTest(name=name):
                tool = registry[name]
                self.assertFalse(tool["schema"]["parameters"]["additionalProperties"])
                implicit = json.loads(tool["handler"](args))
                self.assertEqual((implicit["mode"], implicit["status"]), ("real", "setup-needed"))
                self.assertNotIn("evidence", implicit)
                self.assertNotIn("total", implicit)
                reply = json.loads(tool["handler"]({**args, "scope": "fictional"}))
                self.assertEqual((reply["mode"], reply["heading"]), ("fictional", "Fictional sample"))
                self.assertNotIn("error", reply)
                self.assertIn("evidence", str(reply))
                self.assertNotIn("observations", str(reply))

    def test_amounts_coverage_and_date_validation(self):
        summary = json.loads(companion.summary({}))
        self.assertEqual(summary["total"], summary["supplied"] - summary["debt"])
        self.assertEqual(sum(row["grossValueUsd"] for row in summary["allocation"]), summary["supplied"])
        self.assertIsNone(summary["topMovers"])
        self.assertEqual(json.loads(companion.exposure({"asset": "ETH"}))["grossSharePercent"], "43.57")
        self.assertIn("coverage", json.loads(companion.exposure({"asset": "unknown"})))
        self.assertIn("error", json.loads(companion.recent_movements({"since": "2026-02-30"})))
        self.assertEqual(json.loads(companion.recent_movements({"since": "2026-01-02"}))["rows"], [])

    def test_fictional_attention_starts_with_pending_cases_and_explains_gaps(self):
        cases, *_ = companion.suggestions(companion.fixture_rows())
        data = json.loads(companion.needs_attention({}))
        possible = [item for item in data["items"] if item["kind"] == "possible-transfer"]
        self.assertEqual(len(possible), len(cases))
        self.assertTrue(all(item["status"] == "pending" for item in possible))
        self.assertTrue(any(item.get("source") == "ethereum" for item in data["items"]))

    def test_private_access_requires_boolean_opt_in_and_public_address_is_filtered(self):
        calls = {}
        setting = "true"
        companion.register_tools(SimpleNamespace(get_config=lambda *_: setting,
            register_tool=lambda **spec: calls.update({spec["name"]: spec["handler"]})))
        report = calls["holdings_summary"]
        address = "0x" + "ab" * 20
        with patch.object(companion.companion_live, "private_report", return_value={"evidence": [address]}) as private, patch.object(
                companion.companion_live, "public_report", return_value={"evidence": [address]}) as public:
            self.assertEqual(json.loads(report({"scope": "private"}))["status"], "disabled")
            private.assert_not_called()
            self.assertNotIn(address, report({"scope": "public", "address": address}))
            self.assertIn(address, report({"scope": "public", "address": address, "include_addresses": True}))
            self.assertEqual(public.call_count, 2)
            setting = True
            self.assertNotIn(address, report({"scope": "private", "include_addresses": True}))
            private.assert_called_once_with("summary", {})
            self.assertIn("error", json.loads(report({"scope": "private", "key": "unexpected"})))

    def test_public_history_uses_exact_window_and_projects_only_cited_rows(self):
        live = companion.companion_live
        address = "0x" + "ab" * 20
        row = {"id": "record-1", "kind": "transfer", "asset": "ETH", "quantity": "-0.000000000000000001",
               "at": "2026-01-01", "fee": "0.01", "fee_asset": "ETH", "evidence": ["exact-row"],
               "observations": [{"wallet": address}]}
        snapshot = {"start": 10, "end": 11, "rows": [row], "coverage": [], "gaps": ["Bounded blocks"]}
        args = {"address": address, "start_block": 10, "end_block": 11, "since": "2026-01-01", "year": 2026}
        with patch.object(live.ethereum_review, "review", return_value=snapshot) as read:
            recent = live.public_report("recent_movements", args, get_key=lambda: "fixture")
            read.assert_called_once_with(address, 10, 11, "fixture")
            self.assertNotIn("observations", recent["rows"][0])
            self.assertEqual(recent["rows"][0]["evidence"], ["exact-row"])
            year = live.public_report("explain_year", args, get_key=lambda: "fixture")
            self.assertEqual(year["flows"][0]["recordedOut"], "0.000000000000000001")
            self.assertEqual(year["flows"][0]["recordedFees"], "0.01")
            self.assertEqual(live.public_report("explain_year", {**args, "end_block": 10012},
                                                get_key=lambda: "fixture")["status"], "unavailable")
            self.assertEqual(read.call_count, 2)


class CompanionAssetValueTests(unittest.TestCase):
    def test_asset_value_counts_only_the_named_priced_asset(self):
        live = companion.companion_live
        perf = live.performance
        wallet = {"status": "ready", "fetchedAt": "2026-11-09T06:01:00Z", "balances": [
            {"asset": "ETH", "contract": None, "units": "2"},
            {"asset": "USDC", "contract": perf.USDC_CONTRACTS[1], "units": "10"}]}
        market = {"status": "ready", "candles": [["example", "100", "101", "99", "100.125"]], "closedAt": "2026-11-09T06:00:00+00:00"}
        lines = next(iter(perf.wallet_points([("fictional-eth", 1)], [wallet], market).values()))["lines"]
        self.assertEqual(live.asset_value(lines, "usdc"), "10")
        self.assertEqual(live.asset_value(lines, "ETH"), "200.250")
        self.assertIsNone(live.asset_value(lines, "WETH"))
