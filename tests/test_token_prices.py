"""Curated keyless token list and DefiLlama pricing, with fictional wallets and prices only."""
import importlib
import unittest

from tests.test_kraken_review import package

public_chain = importlib.import_module(f"{package.__name__}.public_chain")
performance = importlib.import_module(f"{package.__name__}.performance")
token_prices = importlib.import_module(f"{package.__name__}.token_prices")

NOW = 1_800_000_000
WALLET = "0x" + "12" * 20
# Checked against each contract's symbol() and decimals() over PublicNode and against DefiLlama coin
# metadata on 2026-11-10. Explorer pages: etherscan.io, basescan.org, arbiscan.io and bscscan.com /token/<address>.
EXPECTED = {
    1: [("ETH", None, 18), ("WETH", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", 18),
        ("USDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
        ("WBTC", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", 8),
        ("USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
        ("DAI", "0x6b175474e89094c44da98b954eedeac495271d0f", 18),
        ("LINK", "0x514910771af9ca656af840dff83e8264ecf986ca", 18),
        ("UNI", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", 18),
        ("stETH", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", 18),
        ("wstETH", "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0", 18)],
    8453: [("ETH", None, 18), ("WETH", "0x4200000000000000000000000000000000000006", 18),
           ("USDC", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 6),
           ("cbBTC", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", 8),
           ("DAI", "0x50c5725949a6f0c72e6c4a641f24049a917db0cb", 18),
           ("cbETH", "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22", 18),
           ("wstETH", "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452", 18),
           ("AERO", "0x940181a94a35a4569e4529a3cdfb74e38fd98631", 18)],
    42161: [("ETH", None, 18), ("WETH", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", 18),
            ("USDC", "0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6),
            ("USDT0", "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", 6),
            ("WBTC", "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f", 8),
            ("DAI", "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1", 18),
            ("ARB", "0x912ce59144191c1204e64559fe8253a0e49e6548", 18),
            ("LINK", "0xf97f4df75117a78c1a5a0dbb814af92458539fb4", 18)],
    56: [("BNB", None, 18), ("WBNB", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", 18),
         ("Binance-Peg USDC", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18),
         ("Binance-Peg USDT", "0x55d398326f99059ff775485246999027b3197955", 18),
         ("BTCB", "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c", 18),
         ("Binance-Peg ETH", "0x2170ed0880ac9a755fd29b2688956bd959f933f8", 18),
         ("CAKE", "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", 18),
         ("FDUSD", "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409", 18)],
    4663: [("ETH", None, 18), ("WETH", "0x0bd7d308f8e1639fab988df18a8011f41eacad73", 18),
           ("USDG", "0x5fc5360d0400a0fd4f2af552add042d716f1d168", 6)],
}


def coin(price=2.5, confidence=0.99, timestamp=NOW - 60):
    return {"price": price, "confidence": confidence, "timestamp": timestamp, "symbol": "FICTIONAL"}


class CuratedListTests(unittest.TestCase):
    def test_addresses_and_decimals_match_the_verified_table(self):
        self.assertEqual({chain: list(network[2]) for chain, network in public_chain.WALLET_NETWORKS.items()}, EXPECTED)

    def test_list_stays_short_and_well_formed(self):
        for chain, (_, _, assets) in public_chain.WALLET_NETWORKS.items():
            self.assertLessEqual(len(assets), 10)
            contracts = [contract for _, contract, _ in assets if contract]
            self.assertEqual(len(contracts), len(set(contracts)))
            self.assertTrue(all(public_chain.ADDR.fullmatch(item) and item == item.lower() for item in contracts))
            self.assertEqual(sum(contract is None for _, contract, _ in assets), 1)

    def test_etherscan_reads_are_paced_for_the_free_plan(self):
        pauses = []
        rows = public_chain.etherscan_wallet_balances(
            lambda params: {"status": "1", "result": "0"}, WALLET, 1, EXPECTED[1], pause=pauses.append)
        self.assertEqual(len(rows["balances"]), 10)
        self.assertEqual(pauses, [public_chain.ETHERSCAN_SPACING] * 9)


class PriceFilterTests(unittest.TestCase):
    def test_confidence_cutoff_is_point_nine(self):
        self.assertIsNone(token_prices.accepted(coin(confidence=0.89), NOW))
        self.assertEqual(token_prices.accepted(coin(confidence=0.9), NOW)["price"], "2.5")

    def test_stale_future_and_malformed_prices_are_refused(self):
        self.assertIsNone(token_prices.accepted(coin(timestamp=NOW - 3601), NOW))
        self.assertIsNone(token_prices.accepted(coin(timestamp=NOW + 5), NOW))
        self.assertIsNotNone(token_prices.accepted(coin(timestamp=NOW - 3600), NOW))
        for bad in ({**coin(), "price": "2.5"}, coin(price=0), coin(price=float("inf")), coin(price=1e13),
                    {**coin(), "confidence": None}, {**coin(), "timestamp": 1.5}, None, []):
            self.assertIsNone(token_prices.accepted(bad, NOW))

    def test_one_request_for_curated_keys_and_extra_coins_are_ignored(self):
        calls = []
        outsider = "0x" + "99" * 20

        def get(url, timeout):
            calls.append((url, timeout))
            keys = url.removeprefix(token_prices.URL).split(",")
            return {"coins": {**{key: coin() for key in keys}, f"ethereum:{outsider}": coin(price=99)}}

        prices = token_prices.current([(WALLET, 1), (WALLET, 4663)], get=get, now=NOW)
        self.assertEqual(len(calls), 1)
        url, timeout = calls[0]
        self.assertTrue(url.startswith("https://coins.llama.fi/prices/current/") and timeout <= 8)
        keys = url.removeprefix(token_prices.URL).split(",")
        self.assertEqual(len(keys), 10)
        self.assertIn("coingecko:ethereum", keys)
        self.assertNotIn(f"ethereum:{outsider}", keys)
        self.assertEqual({chain for chain, _ in prices}, {1})
        self.assertNotIn((1, outsider), prices)
        self.assertIsNone(token_prices.coin_key(1, outsider))
        self.assertIsNone(token_prices.coin_key(4663, None))

    def test_unpriceable_selections_make_no_request(self):
        def get(url, timeout):
            raise AssertionError("no request expected")
        self.assertEqual(token_prices.current([(WALLET, 4663), ("1" * 32, "solana")], get=get, now=NOW), {})

    def test_failed_or_odd_responses_fall_back_to_no_prices(self):
        def broken(url, timeout):
            raise OSError("fictional outage")
        self.assertEqual(token_prices.current([(WALLET, 1)], get=broken, now=NOW), {})
        self.assertEqual(token_prices.current([(WALLET, 1)], get=lambda url, timeout: {"coins": []}, now=NOW), {})


class ValuationTests(unittest.TestCase):
    MARKET = {"status": "ready", "candles": [["example", "100", "101", "99", "100.125"]],
              "closedAt": "2026-11-09T06:00:00+00:00"}

    def wallet(self, chain=1):
        link = dict((asset, contract) for asset, contract, _ in EXPECTED[chain])
        rows = [{"asset": "ETH", "contract": None, "units": "2"}]
        if "LINK" in link:
            rows.append({"asset": "LINK", "contract": link["LINK"], "units": "4"})
        return {"status": "ready", "fetchedAt": "2026-11-09T06:01:00Z", "balances": rows}

    def test_defillama_prices_are_primary_and_labeled(self):
        prices = {(1, None): {"price": "3", "at": "2026-11-09T06:00:00+00:00"},
                  (1, EXPECTED[1][6][1]): {"price": "10", "at": "2026-11-09T06:00:00+00:00"}}
        point = next(iter(performance.wallet_points([(WALLET, 1)], [self.wallet()], self.MARKET, prices).values()))
        self.assertEqual(point["valueUsd"], "46")
        self.assertEqual({line["provider"] for line in point["lines"]}, {"DefiLlama price"})
        self.assertEqual([line["asset"] for line in point["lines"]], ["ETH", "LINK"])
        listed = performance.summary([point], "ready")["points"][0]["prices"]
        self.assertEqual([(item["asset"], item["chainId"], item["price"]) for item in listed], [("ETH", 1, "3"), ("LINK", 1, "10")])

    def test_without_defillama_only_the_fallback_rules_price_and_the_rest_stay_unpriced(self):
        point = next(iter(performance.wallet_points([(WALLET, 1)], [self.wallet()], self.MARKET, {}).values()))
        self.assertEqual([line["asset"] for line in point["lines"]], ["ETH"])
        self.assertEqual(point["lines"][0]["provider"], performance.ETH_AT_WETH)
        arbitrum = next(iter(performance.wallet_points([(WALLET, 42161)], [self.wallet(42161)], {}, {}).values()))
        self.assertEqual(arbitrum["lines"], [])


if __name__ == "__main__":
    unittest.main()
