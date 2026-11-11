import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

from build_demo import SNAPSHOT, build_snapshot

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("holdings_test_plugin", ROOT / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class SampleTests(unittest.TestCase):
    def test_snapshot_has_all_sources_and_matches_generator(self):
        data = build_snapshot()
        self.assertEqual(json.loads(json.dumps(data)), json.loads(SNAPSHOT.read_text()))
        self.assertEqual({row["kind"] for row in data["rows"]},
                         {"buy", "trade_movement", "transfer", "defi_swap"})
        self.assertTrue(all(row["evidence"] and row["observations"] for row in data["rows"]))
        file = (ROOT / "fixtures" / "holdings-fictional-trades.csv").read_bytes()
        self.assertEqual(data["sample_import"], {"sha256": hashlib.sha256(file).hexdigest(), "bytes": len(file)})

    def test_agent_tool_returns_only_requested_fictional_facts_and_evidence(self):
        fee = json.loads(plugin.sample_lookup({"topic": "fees"}))
        self.assertEqual(fee["mode"], "fictional")
        self.assertEqual(fee["rows"][0]["fee"], "0.0002")
        self.assertIn("evidence", fee["rows"][0])
        self.assertNotIn("observations", fee["rows"][0])
        swaps = json.loads(plugin.sample_lookup({"topic": "swaps"}))
        self.assertEqual(len(swaps["rows"]), 2)
        self.assertIn("error", json.loads(plugin.sample_lookup({"topic": "connect"})))

    def test_lookup_keeps_scope_and_validates_utc_ranges(self):
        lookup = lambda **args: json.loads(plugin.sample_lookup({"topic": "fees", **args}))
        self.assertEqual(lookup(source="kraken", start="2026-01-01")["rows"][0]["citation"], "H3")
        self.assertEqual(lookup(source="wallet")["rows"], [])
        self.assertEqual(lookup(start="2026-01-02")["rows"], [])
        self.assertIn("error", lookup(start="2026-01-02", end="2026-01-01"))
        self.assertIn("error", lookup(start="2026-02-30"))
