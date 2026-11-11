"""Fixture checks for the bounded Kraken Desktop review backend."""
import base64
import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.fixtures import MOVEMENT, TRADE

package = ModuleType("holdings_review_fixture")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
review = importlib.import_module(f"{package.__name__}.kraken_review")


class KrakenReviewTests(unittest.TestCase):
    def test_scope_permissions_overlap_and_exact_fees(self):
        calls = []
        def post(path, body, headers):
            calls.append(path.rsplit("/", 1)[-1])
            if path.endswith("GetApiKeyInfo"):
                return {"error": [], "result": {"permissions": ["query-closed-trades", "query-ledger"]}}
            if path.endswith("TradesHistory"):
                return {"error": [], "result": {"count": 1, "trades": {"T-SAMPLE": {
                    "pair": "XXBTZUSD", "time": "1767225600", "type": "buy",
                    "vol": "0.02", "cost": "2", "fee": "0.02"}}}}
            return {"error": [], "result": {"count": 1, "ledger": {"L-SAMPLE": {
                "refid": "T-SAMPLE", "time": "1767225600.123456", "type": "trade",
                "asset": "XXBT", "amount": "0.02", "fee": "0.0002"}}}}
        payload = {"start": "2026-01-01", "end": "2026-01-01", "trades": TRADE, "ledgers": MOVEMENT}
        result = review.review(payload, "fictional", base64.b64encode(b"fictional"), post)
        self.assertEqual(calls, ["GetApiKeyInfo", "TradesHistory", "Ledgers"])
        self.assertEqual((result["overlap"], len(result["rows"])), (2, 2))
        self.assertEqual(result["fee_totals"], {"kraken-asset:XXBT": "0.0002"})
        self.assertEqual(result["unresolved"], [])
        self.assertTrue(all(len(row["evidence"]) == 2 for row in result["rows"]))
        movement = next(row for row in result["rows"] if row["fee_asset"])
        self.assertIn(".123456", movement["at"])
        self.assertEqual({item["time"] for item in movement["observations"]},
                         {"1767225600.123456", "2026-01-01 00:00:00"})

    def test_out_of_scope_file_is_rejected_before_http(self):
        def post(*args):
            self.fail("Unexpected API request")
        payload = {"start": "2026-01-02", "end": "2026-01-02", "trades": TRADE, "ledgers": MOVEMENT}
        with self.assertRaisesRegex(ValueError, "outside"):
            review.review(payload, "fictional", b"fictional", post)
