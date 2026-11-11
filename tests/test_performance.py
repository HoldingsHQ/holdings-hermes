"""Deterministic valuation and import checks with fictional amounts."""
import importlib
import unittest

from tests.test_kraken_review import package

performance = importlib.import_module(f"{package.__name__}.performance")

MARKET = {"status": "ready", "candles": [["example", "100", "101", "99", "100.125"]],
          "closedAt": "2026-11-09T06:00:00+00:00"}
HEAD = "source,asset_id,acquired_utc,quantity,cost_usd,evidence_id\n"


def synthetic_wallet(at, units="1.125", ready=True):
    return {"status": "ready" if ready else "unavailable", "fetchedAt": at, "balances": [
        {"asset": "WETH", "contract": performance.WETH, "units": units},
        {"asset": "ETH", "contract": None, "units": "7"},
    ]}


class PerformanceTests(unittest.TestCase):
    def test_price_exact_weth_and_native_eth_at_the_weth_rate_and_keep_partial_pair(self):
        scope = [("fictional-base", 8453), ("fictional-eth", 1)]
        result = performance.wallet_points(scope, [synthetic_wallet("2026-11-09T06:01:00Z", ready=False),
                                                   synthetic_wallet("2026-11-09T06:01:00Z")], MARKET)
        self.assertEqual(len(result), 2)
        combined = result[performance.scope_id(scope)]
        # 1.125 WETH and 7 native ETH on mainnet, both at the 100.125 WETH/USD close.
        self.assertEqual(combined["valueUsd"], "813.515625")
        self.assertEqual(sorted(line["provider"] for line in combined["lines"]),
                         ["GeckoTerminal WETH/USD", performance.ETH_AT_WETH])
        self.assertFalse(combined["complete"])
        base = performance.wallet_points(scope[:1], [synthetic_wallet("2026-11-09T06:01:00Z")], MARKET)
        self.assertIsNone(next(iter(base.values()))["valueUsd"])
        stale = performance.wallet_points(scope[1:], [synthetic_wallet("2026-11-09T06:01:00Z")], {**MARKET, "stale": True})
        self.assertIsNone(next(iter(stale.values()))["valueUsd"])

    def test_exact_change_and_no_estimate_when_coverage_differs(self):
        scope = [("fictional-wallet", 1)]
        old = next(iter(performance.wallet_points(scope, [synthetic_wallet("2026-11-09T06:01:00Z", "1.25")], MARKET).values()))
        new_market = {**MARKET, "candles": [["example", "100", "110", "99", "110.125"]]}
        new = next(iter(performance.wallet_points(scope, [synthetic_wallet("2026-11-09T08:01:00Z", "2")], new_market).values()))
        change = performance.explain(old, new)
        self.assertEqual((change["marketUsd"], change["unitChangeUsd"]), ("82.50000", "82.59375"))
        self.assertIsNone(performance.explain(old, {**new, "complete": False}))
        self.assertIsNone(performance.explain(old, {**new, "lines": []}))

    def test_canonical_usdc_counts_at_one_dollar_and_other_tokens_stay_unpriced(self):
        wallet = {"status": "ready", "fetchedAt": "2026-11-09T06:01:00Z", "balances": [
            {"asset": "USDC", "contract": performance.USDC_CONTRACTS[1].upper().replace("0X", "0x"), "units": "250.5"},
            {"asset": "LOOKALIKE", "contract": "0x" + "1" * 40, "units": "999"},
        ]}
        point = next(iter(performance.wallet_points([("fictional-eth", 1)], [wallet], {}).values()))
        self.assertEqual(point["valueUsd"], "250.5")
        self.assertEqual(point["lines"][0]["provider"], performance.USDC_AT_PAR)
        self.assertEqual(point["lines"][0]["quoteAt"], wallet["fetchedAt"])
        self.assertEqual(performance.summary([point], "ready")["points"][0]["prices"][0]["asset"], "USDC")
        for chain, contract in ((8453, performance.USDC_CONTRACTS[8453]), (42161, performance.USDC_CONTRACTS[42161])):
            base = {**wallet, "balances": [{"asset": "USDC", "contract": contract, "units": "3"},
                                           {"asset": "ETH", "contract": None, "units": "2"},
                                           {"asset": "WETH", "contract": performance.WETH, "units": "1"}]}
            l2 = next(iter(performance.wallet_points([("fictional-l2", chain)], [base], MARKET).values()))
            self.assertEqual(l2["valueUsd"], "3")
        pegged = {**wallet, "balances": [{"asset": "Binance-Peg USDC", "contract": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "units": "5"}]}
        self.assertIsNone(next(iter(performance.wallet_points([("fictional-bsc", 56)], [pegged], {}).values()))["valueUsd"])
        misplaced = {**wallet, "balances": [{"asset": "USDC", "contract": performance.USDC_CONTRACTS[8453], "units": "5"}]}
        self.assertIsNone(next(iter(performance.wallet_points([("fictional-eth", 1)], [misplaced], {}).values()))["valueUsd"])

    def test_native_eth_needs_a_ready_weth_quote(self):
        wallet = {"status": "ready", "fetchedAt": "2026-11-09T06:01:00Z", "balances": [{"asset": "ETH", "contract": None, "units": "2"}]}
        self.assertIsNone(next(iter(performance.wallet_points([("fictional-eth", 1)], [wallet], {}).values()))["valueUsd"])
        priced = next(iter(performance.wallet_points([("fictional-eth", 1)], [wallet], MARKET).values()))
        self.assertEqual(priced["valueUsd"], "200.250")
        self.assertEqual(performance.priced_asset(priced["lines"][0]["identity"]), "ETH")

    def test_summary_labels_priced_assets_with_their_chain(self):
        wallet = {"status": "ready", "fetchedAt": "2026-11-09T06:01:00Z", "balances": [
            {"asset": "ETH", "contract": None, "units": "2"},
            {"asset": "USDC", "contract": performance.USDC_CONTRACTS[1], "units": "10"}]}
        point = next(iter(performance.wallet_points([("fictional-eth", 1)], [wallet], MARKET).values()))
        prices = performance.summary([point], "ready")["points"][0]["prices"]
        self.assertEqual([(item["asset"], item["chainId"]) for item in prices], [("ETH", 1), ("USDC", 1)])

    def test_scoped_persistence_idempotency_and_locked_storage(self):
        saved = None
        def persist(value):
            nonlocal saved
            saved = value
        def open_store(mode):
            self.assertEqual(mode, "performance-v1")
            return saved, persist
        scope = "fixture"
        other = "unrelated"
        first = performance.point("2026-11-09T06:01:00Z", [], 2, True)
        next_point = {**first, "at": "2026-11-09T06:02:00Z"}
        performance.record({scope: first, other: first}, open_store)
        performance.record({scope: first}, open_store)
        result = performance.record({scope: next_point}, open_store)
        self.assertEqual(list(result), [scope])
        self.assertEqual(len(result[scope]["points"]), 2)
        self.assertEqual(len(saved["streams"][other]), 1)
        def locked(mode):
            raise performance.ProtectedKeyUnavailable()
        result = performance.record({scope: next_point}, locked)
        self.assertEqual(result[scope]["status"], "key-source-unavailable")

    def test_import_groups_by_source_and_preserves_exact_lots(self):
        row = "kraken,kraken-asset:XXBT,2026-11-01,0.123456789012345678,12.123456789,lot-1\n"
        next_row = "kraken,kraken-asset:XXBT,2026-11-02,0.1,4.1,lot-2\n"
        other_row = "binance,kraken-asset:XXBT,2026-11-02,0.5,7.1,lot-1\n"
        binance, kraken = performance.import_basis(HEAD + row + next_row + other_row)
        self.assertEqual((binance["source"], kraken["source"]), ("binance", "kraken"))
        self.assertEqual((kraken["quantity"], kraken["costUsd"]), ("0.223456789012345678", "16.223456789"))
        self.assertEqual([lot["id"] for lot in kraken["lots"]], ["lot-1", "lot-2"])
        for bad in (row + row, row.replace("lot-1", "=1+1"), row.replace("2026-11-01", "2026-11-31")):
            with self.assertRaises(ValueError):
                performance.import_basis(HEAD + bad)
