"""Bybit Global fixtures only. No Bybit account or network is used."""
import hashlib
import hmac
import unittest
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs

import bybit
from ledger import Ledger

START = 1767225600000
PERMISSIONS = {"readOnly": 1, "uta": 1, "permissions": {"Spot": ["SpotTrade"], "Wallet": []}}


def result(**fields):
    return {"retCode": 0, "result": fields}


def fill(identity, **fields):
    return {"symbol": "ETHUSDT", "execType": "Trade", "side": "Buy", "execId": identity,
            "orderId": "fictional-order", "execTime": str(START), "execPrice": "2000",
            "execQty": "0.025", "execValue": "50", "execFee": "0.00012345", "feeCurrency": "ETH",
            "extraFees": "", **fields}


def read(get, end=START + 1000):
    return bybit.read_spot_fills(get, "fictional", "ETHUSDT", "ETH", "USDT", START, end,
                                lambda: START + 2000, "fictional-key", "fictional-secret")


class BybitSpotTests(unittest.TestCase):
    def test_balance_transport_cannot_read_trade_history_or_write(self):
        with patch("bybit.build_opener") as opener:
            for endpoint in (bybit.FILLS_PATH, "/v5/order/create", "/v5/asset/withdraw/create"):
                with self.assertRaises(ValueError):
                    bybit.get_balance_read_only(endpoint, "", {})
            opener.assert_not_called()

    def test_spot_balances_use_signed_unified_read_and_preserve_exact_units(self):
        calls = []

        def get(path, query, headers):
            calls.append(path)
            signature = hmac.new(b"fictional-secret", f"{START}fictional-key5000{query}".encode(), "sha256").hexdigest()
            self.assertEqual(headers["X-BAPI-SIGN"], signature)
            if path == bybit.PERMISSIONS_PATH:
                return result(readOnly=0, uta=1, permissions={"Wallet": ["Withdraw"]}, apiKey="discard")
            self.assertEqual(query, "accountType=UNIFIED")
            return result(list=[{"accountType": "UNIFIED", "totalEquity": "999", "coin": [
                {"coin": "ETH", "walletBalance": "1.00000000000000000001", "locked": "0.00000000000000000002",
                 "spotBorrow": "0.00000000000000000003", "free": "999"},
                {"coin": "USDC", "walletBalance": "0", "locked": "0", "spotBorrow": "0.01"},
            ]}])

        response = bybit.read_spot_balances(get, lambda: START, "fictional-key", "fictional-secret")
        self.assertEqual(calls, [bybit.PERMISSIONS_PATH, bybit.BALANCES_PATH])
        self.assertEqual((response["status"], response["permissionStatus"]), ("ready", "broader"))
        self.assertEqual(set(response), {"status", "permissionStatus", "balances", "fetchedAt"})
        self.assertEqual(response["balances"], [
            {"asset": "ETH", "free": "0.99999999999999999996", "locked": "0.00000000000000000002"},
            {"asset": "USDC", "free": None, "locked": "0"},
        ])

    def test_spot_balances_distinguish_legacy_empty_and_invalid_wallet_data(self):
        def run(permission, wallets):
            seen = []

            def get(path, query, headers):
                seen.append(path)
                return result(**permission) if path == bybit.PERMISSIONS_PATH else result(list=wallets)

            return bybit.read_spot_balances(get, lambda: START, "fictional-key", "fictional-secret"), seen

        self.assertEqual(run({"uta": 0}, []), ({"status": "unsupported-account"}, [bybit.PERMISSIONS_PATH]))
        empty = run(PERMISSIONS, [{"accountType": "UNIFIED", "coin": []}])[0]
        self.assertEqual((empty["status"], empty["permissionStatus"], empty["balances"]), ("ready", "read-only", []))
        row = {"coin": "BTC", "walletBalance": "1", "locked": "0", "spotBorrow": "0"}
        for coins in ([row, row], [{**row, "spotBorrow": None}]):
            self.assertEqual(run(PERMISSIONS, [{"accountType": "UNIFIED", "coin": coins}])[0]["status"], "unavailable")

    def test_signed_cursor_scan_keeps_same_order_fills_and_actual_fee_asset(self):
        calls = []
        first, second = fill("fictional-fill-a"), fill("fictional-fill-b")

        def get(path, query, headers):
            expected = hmac.new(b"fictional-secret", f"{START + 2000}fictional-key5000{query}".encode(),
                                hashlib.sha256).hexdigest()
            self.assertEqual(headers["X-BAPI-SIGN"], expected)
            self.assertEqual(headers["X-BAPI-API-KEY"], "fictional-key")
            calls.append(path)
            if path == bybit.PERMISSIONS_PATH:
                self.assertEqual(query, "")
                return result(**PERMISSIONS)
            params = parse_qs(query)
            self.assertEqual(params["category"], ["spot"])
            self.assertEqual(params["symbol"], ["ETHUSDT"])
            self.assertEqual(params["startTime"], [str(START)])
            self.assertEqual(params["endTime"], [str(START + 1000)])
            if "cursor" not in params:
                return result(category="spot", list=[first], nextPageCursor="1%3A2%2C1%3A2")
            self.assertEqual(params["cursor"], ["1:2,1:2"])
            self.assertIn("cursor=1%3A2%2C1%3A2", query)
            return result(category="spot", list=[first, second], nextPageCursor="")

        rows, coverage = read(get)
        self.assertEqual(calls, [bybit.PERMISSIONS_PATH, bybit.FILLS_PATH, bybit.FILLS_PATH])
        self.assertEqual((coverage["complete"], coverage["pages"]), (True, 2))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row.related for row in rows}), 1)
        self.assertEqual(sum(row.fee for row in rows), Decimal("0.00024690"))
        self.assertEqual({row.fee_asset for row in rows}, {"bybit-asset:ETH"})
        self.assertEqual(dict(rows[0].original)["execId"], "fictional-fill-a")
        self.assertEqual(rows[0].evidence, ("api:bybit-global:fictional:spot:ETHUSDT:fictional-fill-a",))
        ledger = Ledger()
        for row in rows + rows:
            ledger.add(row)
        self.assertEqual(len(list(ledger.entries())), 2)

    def test_rejects_write_or_overbroad_credentials_before_history(self):
        cases = (PERMISSIONS | {"readOnly": 0}, PERMISSIONS | {"uta": 0},
                 PERMISSIONS | {"permissions": {"Spot": ["SpotTrade"], "Wallet": ["Withdraw"]}})
        for permissions in cases:
            with self.subTest(permissions=permissions):
                def get(path, *_):
                    self.assertEqual(path, bybit.PERMISSIONS_PATH)
                    return result(**permissions)
                with self.assertRaisesRegex(ValueError, "read-only"):
                    read(get)

    def test_unknown_fee_currency_and_extra_fees_stay_unresolved(self):
        def get(path, *_):
            if path == bybit.PERMISSIONS_PATH:
                return result(**PERMISSIONS)
            return result(category="spot", list=[fill("a", feeCurrency=""), fill("b", extraFees=[{}]),
                                                 fill("c", execFee="-0.00000003", feeCurrency="USDT")],
                          nextPageCursor="")
        rows, coverage = read(get)
        self.assertEqual(coverage["unresolved_fees"], [rows[0].key, rows[1].key])
        self.assertEqual(rows[0].fee_asset, "")
        self.assertEqual(rows[0].fee, Decimal("0.00012345"))
        self.assertEqual(rows[2].fee, Decimal("-0.00000003"))
        self.assertEqual(rows[2].fee_asset, "bybit-asset:USDT")

    def test_cursor_repeat_and_page_failure_report_incomplete_history(self):
        initial = result(category="spot", list=[fill("a")], nextPageCursor="1%3A2")
        for following, expected in ((initial, "repeated-cursor"),
                                    (result(category="linear", list=[], nextPageCursor=""), "failed-page"),
                                    (result(category="spot", list=[fill("outside", execTime=str(START - 1))],
                                            nextPageCursor=""), "failed-page")):
            with self.subTest(expected=expected):
                pages = iter([initial, following])
                def get(path, *_):
                    return result(**PERMISSIONS) if path == bybit.PERMISSIONS_PATH else next(pages)
                rows, coverage = read(get)
                self.assertEqual((len(rows), coverage["complete"], coverage["gap"]), (1, False, expected))

    def test_invalid_range_and_write_route_stop_before_network(self):
        def unexpected(*_):
            self.fail("Unexpected transport")
        with self.assertRaisesRegex(ValueError, "seven days"):
            read(unexpected, end=START + bybit.MAX_WINDOW_MS)
        with patch.object(bybit, "build_opener", side_effect=unexpected):
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                bybit.get_read_only("/v5/order/create", "", {})
