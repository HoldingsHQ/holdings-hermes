"""Fictional Binance.com Spot fixtures. The transport never reaches an exchange."""
import hashlib
import hmac
import unittest
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs

import binance
from ledger import Ledger

START = 1767225600000
READ_ONLY = {name: False for name in binance.REQUIRED_DENIALS} | {"enableReading": True}


def fill(fill_id, millis, **fields):
    return {"symbol": "ETHUSDT", "id": fill_id, "orderId": 9001, "time": millis,
            "price": "2000.00", "qty": "0.02500000", "quoteQty": "50.00",
            "commission": "0.00012345", "commissionAsset": "BNB", "isBuyer": True,
            "isMaker": False, **fields}


def read(get, start=START, end=START + 100):
    return binance.read_spot_fills(get, "fictional", "ETHUSDT", "ETH", "USDT",
                                  start, end, lambda: START + 1000, "fixture", "fixture")


class BinanceSpotTests(unittest.TestCase):
    def test_balance_read_preserves_units_and_warns_on_extra_key_permissions(self):
        calls = []

        def get(path, signed, headers):
            calls.append(path)
            query, signature = signed.rsplit("&signature=", 1)
            self.assertEqual(signature, hmac.new(b"fixture", query.encode(), hashlib.sha256).hexdigest())
            self.assertEqual(headers, {"X-MBX-APIKEY": "fixture"})
            if path == binance.PERMISSIONS_PATH:
                return READ_ONLY | {"enableWithdrawals": True, "enableSpotAndMarginTrading": True}
            self.assertEqual(parse_qs(query)["omitZeroBalances"], ["true"])
            return {"accountType": "SPOT", "uid": 123, "canTrade": True, "balances": [
                {"asset": "ETH", "free": "0.123456789012345678", "locked": "0.01000000"},
                {"asset": "BTC", "free": "0.00000000", "locked": "0.00000000"},
            ]}

        result = binance.read_spot_balances(get, lambda: START, "fixture", "fixture")
        self.assertEqual(calls, [binance.PERMISSIONS_PATH, binance.BALANCES_PATH])
        self.assertEqual(result, {"status": "ready", "permissionStatus": "broader",
                                  "fetchedAt": "2026-01-01T00:00:00+00:00",
                                  "balances": [{"asset": "ETH", "free": "0.123456789012345678", "locked": "0.01000000"}]})

    def test_balance_read_handles_disabled_unknown_and_read_only_permissions(self):
        def get(path, *unused):
            if path == binance.PERMISSIONS_PATH:
                raise OSError("Fictional transport error")
            return {"accountType": "SPOT", "balances": []}

        result = binance.read_spot_balances(get, lambda: START, "fixture", "fixture")
        self.assertEqual((result["status"], result["permissionStatus"], result["balances"]), ("ready", "unverified", []))

        def disabled(path, *unused):
            self.assertEqual(path, binance.PERMISSIONS_PATH)
            return {"enableReading": False}

        self.assertEqual(binance.read_spot_balances(disabled, lambda: START, "fixture", "fixture"), {"status": "read-disabled"})
        result = binance.read_spot_balances(lambda path, *_: READ_ONLY if path == binance.PERMISSIONS_PATH else
                                            {"accountType": "SPOT", "balances": []}, lambda: START, "fixture", "fixture")
        self.assertEqual(result["permissionStatus"], "read-only")

    def test_balance_read_rejects_partial_or_non_spot_data(self):
        row = {"asset": "ETH", "free": "0", "locked": "0"}
        for account in ({"accountType": "MARGIN", "balances": [row]},
                        {"accountType": "SPOT", "balances": [row, row]},
                        {"accountType": "SPOT", "balances": [{**row, "locked": "-1"}]},
                        {"accountType": "SPOT", "balances": [{**row, "free": 0.01}]}):
            with self.subTest(account=account):
                result = binance.read_spot_balances(lambda path, *_: READ_ONLY if path == binance.PERMISSIONS_PATH else account,
                                                    lambda: START, "fixture", "fixture")
                self.assertEqual(result, {"status": "unavailable", "permissionStatus": "read-only"})

    @patch.object(binance, "PAGE_SIZE", 3)
    def test_repeats_boundary_and_keeps_same_order_fills_and_exact_fees(self):
        first = fill(10, START)
        boundary = fill(11, START + 1)
        same_millisecond = fill(12, START + 1)
        later = fill(13, START + 2)
        calls = []

        def get(path, signed, headers):
            query, signature = signed.rsplit("&signature=", 1)
            self.assertEqual(signature, hmac.new(b"fixture", query.encode(), hashlib.sha256).hexdigest())
            self.assertEqual(headers, {"X-MBX-APIKEY": "fixture"})
            calls.append(path)
            if path == binance.PERMISSIONS_PATH:
                return READ_ONLY
            params = parse_qs(query)
            self.assertEqual(params["symbol"], ["ETHUSDT"])
            self.assertEqual(params["endTime"], [str(START + 100)])
            pages = {START: [first, boundary, same_millisecond],
                     START + 1: [boundary, same_millisecond, later],
                     START + 2: [later]}
            return pages[int(params["startTime"][0])]

        rows, coverage = read(get)
        self.assertEqual(calls, [binance.PERMISSIONS_PATH] + [binance.FILLS_PATH] * 3)
        self.assertTrue(coverage["complete"])
        self.assertEqual(coverage["pages"], 3)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row.related for row in rows}), 1)
        self.assertEqual(sum(row.fee for row in rows), Decimal("0.00049380"))
        self.assertEqual({row.fee_asset for row in rows}, {"binance-asset:BNB"})
        self.assertEqual(dict(rows[0].original)["qty"], "0.02500000")
        self.assertEqual(rows[0].evidence, ("api:binance-global:fictional:spot:ETHUSDT:10",))
        ledger = Ledger()
        for row in rows + rows:
            ledger.add(row)
        self.assertEqual(len(list(ledger.entries())), 4)

    def test_rejects_missing_or_broad_permissions_before_reading_fills(self):
        for flags in ({"enableReading": True}, READ_ONLY | {"enableInternalTransfer": True},
                      READ_ONLY | {"futurePermission": True}, READ_ONLY | {"enableFixApiTrade": "true"}):
            with self.subTest(flags=flags):
                def get(path, *unused):
                    self.assertEqual(path, binance.PERMISSIONS_PATH)
                    return flags
                with self.assertRaisesRegex(ValueError, "read-only"):
                    read(get)

    @patch.object(binance, "PAGE_SIZE", 2)
    def test_marks_saturated_millisecond_without_skipping(self):
        def get(path, *unused):
            if path == binance.PERMISSIONS_PATH:
                return READ_ONLY
            return [fill(10, START), fill(11, START)]
        rows, coverage = read(get)
        self.assertEqual(len(rows), 2)
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["gap"], "saturated-millisecond")

    @patch.object(binance, "PAGE_SIZE", 2)
    def test_preserves_earlier_page_when_later_page_is_outside_scope(self):
        pages = iter([[fill(10, START), fill(11, START + 1)], [fill(12, START + 101)]])
        def get(path, *unused):
            return READ_ONLY if path == binance.PERMISSIONS_PATH else next(pages)
        rows, coverage = read(get)
        self.assertEqual(len(rows), 2)
        self.assertEqual(coverage["failed_pages"], [2])
        self.assertFalse(coverage["complete"])

    def test_invalid_scope_and_write_route_never_reach_network(self):
        def unexpected(*unused):
            self.fail("Unexpected transport")
        with self.assertRaisesRegex(ValueError, "24 hours"):
            read(unexpected, end=START + binance.DAY_MS)
        with patch.object(binance, "build_opener", side_effect=unexpected):
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                binance.get_read_only("/api/v3/order", "", {})
