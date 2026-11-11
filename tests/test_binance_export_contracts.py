"""Compare fictional exports to documented response fields without Binance access."""
import csv
import importlib
import json
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from tests.test_binance import READ_ONLY
from tests.test_kraken_review import package

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
UTC_PLUS_7 = timezone(timedelta(hours=7))
review = importlib.import_module(f"{package.__name__}.binance_review")


def fixture_json(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def utc_second(at):
    return at.astimezone(timezone.utc).replace(microsecond=0)


def csv_income(row):
    # Candidate mapping only. The observed export was empty, so these CSV
    # values intentionally assume Binance's documented income type and ID.
    at = datetime.strptime(row["Time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC_PLUS_7)
    return (row["Type"], row["Transaction ID"], row["Asset"],
            row["Symbol"], utc_second(at), Decimal(row["Amount"]))


def api_income(row):
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    at = epoch + timedelta(milliseconds=row["time"])
    return (row["incomeType"], str(row["tranId"]), row["asset"],
            row["symbol"], utc_second(at), Decimal(row["income"]))


class BinanceExportContracts(unittest.TestCase):
    def test_spot_fixtures_reconcile_two_unique_fills_and_surface_unmatched_rows(self):
        text = (FIXTURES / "binance-fictional-spot-trade-history.csv").read_text(encoding="utf-8")
        mock_api = fixture_json("binance-fictional-spot-api.json")

        def get(path, *_):
            return READ_ONLY if path.endswith("apiRestrictions") else mock_api

        snapshot = review.review({"csv": text, "offset": "+07:00"}, "fictional", "fictional", get)
        self.assertEqual((snapshot["start"], snapshot["end"]), ("2026-01-01", "2026-01-01"))
        self.assertEqual((snapshot["api_rows"], snapshot["csv_rows"], snapshot["overlap"]), (3, 3, 2))
        self.assertEqual(snapshot["multi_fill_orders"], 1)
        self.assertEqual(len(snapshot["unresolved"]), 2)
        self.assertEqual(len(snapshot["unresolved_observations"]), 1)
        self.assertEqual(len(snapshot["rows"]), 3)
        self.assertEqual(snapshot["fee_totals"], {
            "binance-asset:BNB": "0.00024690",
            "binance-asset:USDT": "0.0200",
        })
        self.assertEqual(sorted(len(row["evidence"]) for row in snapshot["rows"]), [1, 2, 2])

    def test_futures_candidate_maps_signed_income_without_treating_it_as_fills(self):
        with (FIXTURES / "binance-fictional-futures-transaction-history.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, ["Time", "Type", "Amount", "Asset", "Symbol", "Transaction ID"])
            csv_rows = [csv_income(row) for row in reader]
        api_rows = [api_income(row) for row in fixture_json("binance-fictional-futures-income-api.json")]

        self.assertEqual(Counter(csv_rows), Counter(api_rows))
        self.assertEqual(len({row[1] for row in csv_rows}), 3)
        self.assertEqual(len({row[:2] for row in csv_rows}), 4)
        self.assertEqual(sum(row[-1] for row in api_rows if row[0] == "FUNDING_FEE"), Decimal("-0.125"))
        # Even with the same typed transaction ID, an amount discrepancy stays visible.
        first = csv_rows[0]
        changed = first[:-1] + (abs(first[-1]),)
        self.assertEqual(list((Counter([changed, *csv_rows[1:]]) - Counter(api_rows)).elements()), [changed])
