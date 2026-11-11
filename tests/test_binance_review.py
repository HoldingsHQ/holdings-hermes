"""Fictional examples matching the observed Binance.com export columns."""
import unittest
from urllib.parse import parse_qs

from tests.test_binance import READ_ONLY, START, fill
from tests.test_kraken_review import package
import importlib

review = importlib.import_module(f"{package.__name__}.binance_review")
HEADER = ",".join(review.COLUMNS) + "\n"
ROW = "9001,2026-01-01 07:00:00,ETHUSDT,BUY,2000.00,0.02500000ETH,50.00USDT,0.00012345BNB,,\n"


class BinanceReviewTests(unittest.TestCase):
    def test_offset_bounds_and_exact_unique_matches(self):
        def get(path, signed, _headers):
            if "apiRestrictions" in path:
                return READ_ONLY
            query = parse_qs(signed)
            self.assertEqual(query["startTime"], [str(START)])
            self.assertEqual(query["endTime"], [str(START + 86_400_000 - 1)])
            self.assertEqual(query["symbol"], ["ETHUSDT"])
            return [fill(10, START + 312), fill(11, START + 472, qty="0.030", quoteQty="60")]
        second = ROW.replace("0.02500000ETH", "0.030ETH").replace("50.00USDT", "60USDT")
        other_day = ROW.replace("2026-01-01 07:00:00", "2026-01-01 06:59:59")
        result = review.review({"csv": HEADER + other_day + ROW + second, "offset": "+07:00"}, "fixture", "fixture", get)
        self.assertEqual((result["start"], result["symbol"]), ("2026-01-01", "ETHUSDT"))
        self.assertEqual((result["overlap"], result["api_rows"], result["csv_rows"]), (2, 2, 2))
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["multi_fill_orders"], 1)
        self.assertEqual(result["fee_totals"], {"binance-asset:BNB": "0.00024690"})
        self.assertTrue(all(len(row["evidence"]) == 2 for row in result["rows"]))

    def test_ambiguous_export_keeps_originals_without_adding_fees(self):
        def get(path, *_):
            return READ_ONLY if "apiRestrictions" in path else [fill(10, START)]
        result = review.review({"csv": HEADER + ROW + ROW, "offset": "+07:00"}, "fixture", "fixture", get)
        self.assertEqual(result["overlap"], 0)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(len(result["unresolved_observations"]), 2)
        self.assertEqual(result["fee_totals"], {"binance-asset:BNB": "0.00012345"})

    def test_invalid_export_fails_before_api(self):
        def get(*_):
            self.fail("Unexpected Binance request")
        for payload in ({"csv": HEADER + ROW, "offset": "UTC+7"},
                        {"csv": HEADER, "offset": "+07:00"},
                        {"csv": HEADER + '"', "offset": "+07:00"}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                review.review(payload, "fixture", "fixture", get)
