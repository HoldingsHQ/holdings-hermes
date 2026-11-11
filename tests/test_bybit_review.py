"""Documented Bybit response shapes exercised without credentials or network."""
import importlib
import unittest
from urllib.parse import parse_qs
from pathlib import Path

from tests.test_bybit import PERMISSIONS, START, fill, result
from tests.test_kraken_review import package

review = importlib.import_module(f"{package.__name__}.bybit_review")


class BybitReviewTests(unittest.TestCase):
    def test_api_only_scan_keeps_all_returned_markets_and_marks_unclear_fees(self):
        def get(path, query, _headers):
            if path.endswith("query-api"):
                return result(**PERMISSIONS)
            params = parse_qs(query)
            self.assertNotIn("symbol", params)
            self.assertEqual(params["startTime"], [str(START)])
            self.assertEqual(params["endTime"], [str(START + 86_400_000 - 1)])
            return result(category="spot", list=[
                fill("a"), fill("b", symbol="SOLUSDT", feeCurrency="SOL", extraFees=[{}]),
            ], nextPageCursor="")

        snapshot = review.review({"date": "2026-01-01"}, "fictional-key", "fictional-secret", get)
        self.assertEqual(snapshot["mode"], "bybit-spot")
        self.assertEqual(snapshot["api_rows"], 2)
        self.assertEqual({row["asset"] for row in snapshot["rows"]}, {"bybit-pair:ETHUSDT", "bybit-pair:SOLUSDT"})
        self.assertEqual(snapshot["unresolved"], ["bybit-global:review:spot:SOLUSDT:b"])
        self.assertEqual(snapshot["fee_totals"], {"bybit-asset:ETH": "0.00012345"})
        self.assertNotIn("csv_rows", snapshot)

    def test_invalid_or_ambiguous_dates_fail_before_transport(self):
        def get(*_):
            self.fail("Unexpected request")
        for payload in ({"date": "tomorrow"}, {"date": "9999-01-01"}, {"date": "2026-01-01", "csv": "x"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                review.review(payload, "fictional-key", "fictional-secret", get)

    def test_csv_keeps_exact_ids_amounts_and_minute_precision(self):
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "bybit-fictional-spot-trade-history.csv"
        text = fixture.read_text(encoding="utf-8")
        header, row, _duplicate, other = text.splitlines(keepends=True)
        snapshot = review.csv_review({"csv": text})
        self.assertEqual(snapshot["mode"], "bybit-csv")
        self.assertEqual((snapshot["csv_rows"], len(snapshot["rows"])), (3, 2))
        first = snapshot["rows"][0]
        self.assertEqual(first["at"], "2026-01-01T08:12+00:00")
        self.assertTrue(first["id"].endswith(":9000000000000000001"))
        self.assertEqual(len(first["evidence"]), 2)
        self.assertEqual(snapshot["fee_totals"], {"bybit-asset:ETH": "0.00001", "bybit-asset:USDT": "0.0300"})
        self.assertNotIn("api_rows", snapshot)
        conflicting = row.replace("0.00001", "0.00002")
        snapshot = review.csv_review({"csv": header + row + conflicting + other})
        self.assertEqual((len(snapshot["rows"]), len(snapshot["conflicts"])), (1, 1))
        self.assertEqual(snapshot["fee_totals"], {"bybit-asset:USDT": "0.0300"})
