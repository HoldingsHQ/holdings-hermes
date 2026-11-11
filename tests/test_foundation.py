"""Fictional account and receipt payloads. No network or credentials."""
import base64
import io
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from ethereum import FACTORY, USDC, WETH, bounded_history, classify_swap, inspect_swap, read_only_rpc, wallet_balances
from kraken import BALANCE_PATH, KEY_INFO_PATH, normalize, post_read_only, read_export, read_history, read_spot_balances, reconcile, sign, verify_query_permissions
from ledger import Ledger

from tests.fixtures import (ACCOUNT, BLOCK, BLOCK_HASH, HASH, MOVEMENT, POOL, TRADE, WALLET,
                            example, topic)


class FoundationTests(unittest.TestCase):
    def test_keyless_wallet_balances_use_one_safe_block_and_exact_units(self):
        reads = []

        def send(method, params):
            reads.append((method, params))
            if method == "eth_chainId":
                return "0x1"
            if method == "eth_getBlockByNumber":
                self.assertEqual(params, ["safe", False])
                return {"number": BLOCK}
            if method == "eth_getBalance":
                self.assertEqual(params, [WALLET, BLOCK])
                return hex(1234567890123456789)
            self.assertEqual(params[0]["data"], "0x70a08231" + WALLET[2:].zfill(64))
            self.assertEqual(params[1], BLOCK)
            if params[0]["to"] == WETH:
                raise OSError("fictional RPC failure")
            self.assertEqual(params[0]["to"], USDC)
            return f"0x{1234567:064x}"

        result = wallet_balances(read_only_rpc(send), WALLET.upper().replace("0X", "0x"))
        self.assertEqual((result["status"], result["block"]), ("ready", str(int(BLOCK, 16))))
        self.assertEqual(result["balances"], [
            {"asset": "ETH", "units": "1.234567890123456789"},
            {"asset": "WETH", "units": None},
            {"asset": "USDC", "units": "1.234567"},
        ])
        self.assertEqual([method for method, _ in reads],
                         ["eth_chainId", "eth_getBlockByNumber", "eth_getBalance", "eth_call", "eth_call"])
        with self.assertRaises(ValueError):
            wallet_balances(read_only_rpc(lambda *_: "0x2"), WALLET)

    def test_wallet_balance_allowlist_rejects_unbounded_contract_calls(self):
        rpc = read_only_rpc(lambda *_: self.fail("Unexpected provider call"))
        encoded = "0x70a08231" + WALLET[2:].zfill(64)
        for method, params in (
            ("eth_getBalance", [WALLET, "latest"]),
            ("eth_call", [{"to": WALLET, "data": encoded}, BLOCK]),
            ("eth_call", [{"to": USDC, "data": "0xa9059cbb" + WALLET[2:].zfill(64)}, BLOCK]),
        ):
            with self.assertRaises(ValueError):
                rpc(method, params)

    def test_chain_wallet_transport_keeps_asset_identity_and_block_scope(self):
        token = "0x" + "34" * 20
        values = {"eth_chainId": "0x38", "eth_getBlockByNumber": {"number": BLOCK},
                  "eth_getBalance": "0x1", "eth_call": f"0x{1234567890123456789:064x}"}
        calls = []

        def send(method, params):
            calls.append((method, params))
            return values[method]

        rpc = read_only_rpc(send, balance_tokens={token}, wallet_only=True, wallet_block="latest")
        result = wallet_balances(rpc, WALLET, 56, (("BNB", None, 18), ("Binance-Peg USDC", token, 18)), "latest")
        self.assertEqual(result["balances"][1], {"asset": "Binance-Peg USDC", "units": "1.234567890123456789"})
        self.assertEqual(calls[1], ("eth_getBlockByNumber", ["latest", False]))
        self.assertEqual(calls[-1][1][1], BLOCK)
        for method, params in (
            ("eth_getBlockByNumber", ["safe", False]), ("eth_getTransactionByHash", [HASH]),
            ("eth_call", [{"to": USDC, "data": "0x70a08231" + WALLET[2:].zfill(64)}, BLOCK]),
        ):
            with self.assertRaises(ValueError):
                rpc(method, params)
        with self.assertRaises(ValueError):
            wallet_balances(rpc, WALLET, 1)

    def test_csv_api_overlap_and_actual_fee(self):
        ledger = Ledger()
        for _ in range(2):
            for row in read_export(TRADE, "trades", ACCOUNT):
                ledger.add(row)
        def post(path, body, headers):
            self.assertEqual(path, "/0/private/TradesHistory")
            self.assertIn(b"ofs=0", body)
            self.assertIn(b"consolidate_taker=false", body)
            self.assertIn("API-Sign", headers)
            return {"error": [], "result": {"count": 1, "trades": {
                "T-SAMPLE": {"pair": "XXBTZUSD", "time": "1767225600", "type": "buy",
                             "vol": "0.02", "cost": "2", "fee": "0.02"}}}}
        rows, coverage = read_history(post, "TradesHistory", ACCOUNT, 0, 1767225600,
                                      lambda: 1, "fictional", base64.b64encode(b"fictional"))
        for row in rows + read_export(MOVEMENT, "ledger", ACCOUNT):
            ledger.add(row)
        self.assertTrue(coverage["complete"])
        movement, trade = list(ledger.entries())
        self.assertEqual(trade.quantity, Decimal("0.02"))
        self.assertEqual(trade.cost, Decimal(2))
        self.assertEqual(trade.fee, 0)
        self.assertEqual(len(trade.evidence), 2)
        self.assertEqual(len(ledger.observations(trade.key)), 2)
        self.assertEqual((movement.fee_asset, movement.fee),
                         ("kraken-asset:XXBT", Decimal("0.0002")))

    def test_partial_fills_with_same_order_remain_separate(self):
        header, fill = TRADE.splitlines()
        csv = "\ufeff" + "\n".join((header, fill, fill.replace("T-SAMPLE", "T-SECOND")))
        ledger = Ledger()
        for _ in range(2):
            for entry in read_export(csv, "trades", ACCOUNT):
                ledger.add(entry)
        self.assertEqual({row.key.rsplit(":", 1)[-1] for row in ledger.entries()},
                         {"T-SAMPLE", "T-SECOND"})
        self.assertTrue(all(row.quantity == Decimal("0.02") for row in ledger.entries()))

    def test_kraken_transport_preserves_decimal_json_timestamps(self):
        response = io.BytesIO(b'{"time":1767225600.123456}')
        opener = SimpleNamespace(open=lambda request, timeout: response)
        with patch("kraken.build_opener", return_value=opener):
            self.assertEqual(post_read_only("/0/private/Ledgers", b"nonce=1", {})["time"],
                             Decimal("1767225600.123456"))

    def test_kraken_history_permissions_allow_query_funds_and_reject_other_extra_scope(self):
        secret = base64.b64encode(b"fictional")
        def verify(permissions):
            def post(path, body, headers):
                self.assertEqual((path, body), (KEY_INFO_PATH, b"nonce=1"))
                self.assertIn("API-Sign", headers)
                return {"error": [], "result": {"permissions": permissions}}
            return verify_query_permissions(post, lambda: 1, "fictional", secret)
        verify(["query-closed-trades", "query-ledger"])
        verify(["query-closed-trades", "query-ledger", "query-funds"])
        for extra in ("modify-trades", "withdraw-funds", "query-open-trades"):
            with self.assertRaisesRegex(ValueError, "query-only"):
                verify(["query-closed-trades", "query-ledger", extra])

    def test_kraken_spot_balances_preserve_precision_and_warn_without_blocking(self):
        secret = b"ZmljdGlvbmFs"
        nonce = iter((100, 101))
        seen = []

        def post(path, body, headers):
            seen.append(path)
            current = 99 + len(seen)
            self.assertEqual(body, f"nonce={current}".encode())
            self.assertEqual(headers, {"API-Key": "fixture", "API-Sign": sign(path, current, body, secret)})
            if path == KEY_INFO_PATH:
                return {"error": [], "result": {"permissions": ["query-funds", "withdraw-funds"], "apiKey": "discard"}}
            return {"error": [], "result": {
                "XXBT": {"balance": Decimal("1.00000000000000000001"), "hold_trade": "0.00000000000000000002"},
                "USDT.F": {"balance": "2.0", "hold_trade": "0", "credit": "1", "credit_used": "0"},
                "EMPTY": {"balance": "0", "hold_trade": "0"},
            }}

        result = read_spot_balances(post, lambda: next(nonce), "fixture", secret)
        self.assertEqual(seen, [KEY_INFO_PATH, BALANCE_PATH])
        self.assertEqual((result["status"], result["permissionStatus"]), ("ready", "broader"))
        self.assertEqual(set(result), {"status", "permissionStatus", "balances", "fetchedAt"})
        self.assertEqual(result["balances"], [
            {"asset": "USDT.F", "free": None, "locked": "0"},
            {"asset": "XXBT", "free": "0.99999999999999999999", "locked": "0.00000000000000000002"},
        ])

    def test_kraken_spot_balances_handle_missing_permissions_and_malformed_holds(self):
        def run(permissions, fields):
            seen = []

            def post(path, body, headers):
                seen.append(path)
                return {"error": [], "result": ({"permissions": permissions} if path == KEY_INFO_PATH
                                                else {"XXBT": fields})}

            return read_spot_balances(post, lambda: len(seen), "fixture", b"ZmljdGlvbmFs"), seen

        result, seen = run(["query-ledger"], {})
        self.assertEqual((result, seen), ({"status": "read-disabled"}, [KEY_INFO_PATH]))
        self.assertEqual(run(["query-funds"], {"balance": "1"})[0],
                         {"status": "unavailable", "permissionStatus": "read-only"})
        self.assertEqual(run(None, {"balance": "1", "hold_trade": "0"})[0]["permissionStatus"], "unverified")

    def test_kraken_retains_first_page_after_failed_page_and_reconciles_transfer(self):
        move = {"refid": "REF", "time": "1767225600", "type": "transfer",
                "asset": "XXBT", "amount": "-0.4", "fee": "0.001"}
        def post(path, body, headers):
            if b"ofs=1" in body:
                raise TimeoutError("fictional upstream timeout")
            return {"error": [], "result": {"count": 2, "ledger": {"MOVE-1": move}}}
        api, coverage = read_history(post, "Ledgers", ACCOUNT, 1767225600, 1767225601,
                                     lambda: 1, "fictional", base64.b64encode(b"fictional"))
        csv = ("txid,refid,time,type,asset,amount,fee\n"
               "MOVE-1,REF,2026-01-01 00:00:00,transfer,XXBT,-0.4,0.001\n")
        merged = reconcile(api, read_export(csv, "ledger", ACCOUNT) * 2)
        self.assertEqual((coverage["pages"], coverage["failed_pages"], coverage["complete"]),
                         (1, [2], False))
        self.assertEqual(merged.conflicts(), {})
        row, = merged.entries()
        self.assertEqual((row.kind, row.quantity, row.fee_asset, row.fee),
                         ("transfer", Decimal("-0.4"), "kraken-asset:XXBT", Decimal("0.001")))
        self.assertEqual(len(row.evidence), 2)

    def test_conflicting_csv_observations_stay_out_of_entries(self):
        ledger = Ledger()
        for csv in (TRADE, TRADE.replace(",0.02,L-SAMPLE", ",0.01,L-SAMPLE")):
            ledger.add(read_export(csv, "trades", ACCOUNT)[0])
        self.assertEqual(len(ledger.conflicts()), 1)
        self.assertEqual(list(ledger.entries()), [])

    def test_conflict_evidence_preserves_api_and_file_details(self):
        identity = f"kraken:{ACCOUNT}:ledger:L-SAMPLE"
        api_row = normalize(ACCOUNT, "ledger", "L-SAMPLE", {
            "refid": "T-SAMPLE", "time": "1767225600.123456", "type": "trade",
            "asset": "XXBT", "amount": "0.0199", "fee": "0.0003",
        }, "api")
        file_row, = read_export(MOVEMENT, "ledger", ACCOUNT)
        ledger = reconcile([api_row], [file_row, file_row])
        self.assertEqual(list(ledger.entries()), [])
        evidence = ledger.conflict_evidence(ledger.conflicts())[identity]
        by_source = {row["evidence"][0].split(":")[0]: row for row in evidence}
        self.assertEqual(set(by_source), {"api", "csv"})
        self.assertEqual({row["originalId"] for row in evidence}, {identity})
        self.assertEqual((by_source["api"]["quantity"], by_source["api"]["fee"]), ("0.0199", "0.0003"))
        self.assertEqual((by_source["csv"]["quantity"], by_source["csv"]["fee"]), ("0.02", "0.0002"))
        self.assertIn(".123456", by_source["api"]["at"])
        self.assertEqual(by_source["csv"]["original"]["time"], "2026-01-01 00:00:00")

    def test_invalid_import_and_write_endpoint_are_rejected(self):
        with self.assertRaises(ValueError):
            read_export("bad,header\n1,2\n", "trades", ACCOUNT)
        for method in ("AddOrder", "Withdraw"):
            with self.assertRaises(ValueError):
                read_history(None, method, ACCOUNT, 0, 1, None, "", "")

    def test_history_marks_capped_stream(self):
        def get(params):
            if params["action"] == "txlist":
                return {"status": "1", "result": [{"hash": HASH, "blockNumber": "16",
                        "from": WALLET, "to": POOL}]}
            return {"status": "0", "message": "No transactions found", "result": []}
        rows, coverage = bounded_history(get, WALLET, 16, 17, page_size=1, max_pages=1)
        self.assertEqual(rows["txlist"][0]["hash"], HASH)
        self.assertFalse(coverage["streams"]["txlist"]["end_of_results"])
        self.assertTrue(coverage["streams"]["tokentx"]["end_of_results"])

    def test_failed_page_retains_prior_rows_and_reports_gap(self):
        def get(params):
            if params["action"] == "tokentx":
                return {"status": "0", "message": "No transactions found", "result": []}
            if params["page"] == 1:
                return {"status": "1", "result": [{"hash": HASH, "blockNumber": "16",
                        "from": WALLET, "to": POOL}]}
            raise TimeoutError("simulated upstream timeout")
        rows, coverage = bounded_history(get, WALLET, 16, 17, page_size=1)
        self.assertEqual(len(rows["txlist"]), 1)
        self.assertEqual(coverage["streams"]["txlist"]["failed_pages"], [2])
        self.assertFalse(coverage["streams"]["txlist"]["end_of_results"])
        self.assertTrue(coverage["streams"]["tokentx"]["end_of_results"])

    def test_rpc_boundary_rejects_mutations_and_arbitrary_calls_before_transport(self):
        calls = []
        rpc = read_only_rpc(lambda *args: calls.append(args))
        for method in ("eth_sendTransaction", "eth_sendRawTransaction", "eth_sign", "personal_sign"):
            with self.assertRaises(ValueError):
                rpc(method, [])
        with self.assertRaises(ValueError):
            rpc("eth_call", [{"to": WALLET, "data": "0x"}, BLOCK])
        self.assertEqual(calls, [])
        rpc("eth_getTransactionReceipt", [HASH])
        self.assertEqual(calls, [("eth_getTransactionReceipt", [HASH])])

    def test_public_swap_proof_verifies_block_and_labels_unsupported_wallet(self):
        tx, receipt = example()
        chain = {
            "eth_chainId": "0x1", "eth_getTransactionByHash": tx,
            "eth_getTransactionReceipt": receipt, "eth_getBlockByNumber": {
                "hash": BLOCK_HASH, "number": BLOCK, "timestamp": hex(1767225600),
            },
        }
        rpc = lambda method, args: chain[method]
        factory = lambda method, args: "0x1" if method == "eth_chainId" else topic(POOL)
        verified = inspect_swap(rpc, factory, WALLET, HASH)
        self.assertEqual(verified["status"], "verified")
        self.assertEqual([Decimal(row["quantity"]) for row in verified["rows"]],
                         [Decimal("-1"), Decimal("0.01")])
        self.assertEqual(len(verified["rows"][0]["evidence"]), 3)
        unsupported = inspect_swap(rpc, factory, POOL, HASH)
        self.assertEqual((unsupported["status"], unsupported["rows"]), ("unsupported", []))
        chain["eth_getBlockByNumber"]["hash"] = HASH
        self.assertEqual(inspect_swap(rpc, factory, WALLET, HASH)["status"], "unavailable")

    def test_swap_requires_factory_and_both_wallet_transfers(self):
        def rpc(method, params):
            self.assertEqual((method, params[0]["to"], params[1]), ("eth_call", FACTORY, BLOCK))
            return topic(POOL)
        tx, receipt = example()
        rows = classify_swap(rpc, WALLET, tx, receipt, 1767225600)
        self.assertEqual([x.quantity for x in rows], [Decimal("-1"), Decimal("0.01")])
        self.assertEqual(len(rows[0].evidence), 3)
        self.assertEqual(classify_swap(lambda *args: topic(WALLET), WALLET, tx, receipt, 1), ())
        receipt["logs"].pop()
        self.assertEqual(classify_swap(rpc, WALLET, tx, receipt, 1), ())


if __name__ == "__main__":
    unittest.main()
