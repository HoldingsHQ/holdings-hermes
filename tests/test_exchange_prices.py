"""Keyless exchange ticker pricing with fictional quotes only."""
import importlib
import json
import unittest
from urllib.parse import parse_qs, urlsplit

from tests.test_kraken_review import package

exchange_prices = importlib.import_module(f"{package.__name__}.exchange_prices")
performance = importlib.import_module(f"{package.__name__}.performance")

NOW = 1_800_000_000_000


class ExchangePriceTests(unittest.TestCase):
    def test_binance_picks_listed_symbols_then_checks_volume_and_close_time(self):
        calls = []

        def get(url, timeout):
            calls.append(url)
            if url == exchange_prices.URLS["binance"]:
                return [{"symbol": "ETHUSDC", "price": "2"}, {"symbol": "BTCUSDT", "price": "3"}, {"symbol": "OLDUSDT", "price": "4"},
                        {"symbol": "DUSTUSDT", "price": "5"}]
            query = parse_qs(urlsplit(url).query)
            self.assertEqual(json.loads(query["symbols"][0]), ["BTCUSDT", "DUSTUSDT", "ETHUSDC", "OLDUSDT"])
            return [{"symbol": "ETHUSDC", "lastPrice": "2.5", "volume": "10", "closeTime": NOW - 1000},
                    {"symbol": "BTCUSDT", "lastPrice": "30", "volume": "1", "closeTime": NOW + 2000},
                    {"symbol": "OLDUSDT", "lastPrice": "4", "volume": "1", "closeTime": NOW - 3_600_001},
                    {"symbol": "DUSTUSDT", "lastPrice": "0", "volume": "1", "closeTime": NOW}]

        prices = exchange_prices.quotes("binance", ["ETH", "BTC", "OLD", "DUST", "USDT", "NOPE"], get=get, now_ms=NOW)
        self.assertEqual(len(calls), 2)
        self.assertEqual(set(prices), {"ETH", "BTC", "USDT"})
        self.assertEqual(prices["ETH"]["provider"], "Binance ETH/USDC price")
        self.assertEqual(prices["BTC"]["provider"], "Binance BTC/USDT price")
        self.assertEqual(prices["USDT"], {"price": "1", "at": "2027-01-15T08:00:00+00:00", "provider": "USDT counted at $1"})

    def test_kraken_needs_trades_and_volume_and_names_btc_plainly(self):
        data = {"error": [], "result": {
            "XXBTZUSD": {"c": ["100.5", "1"], "v": ["1", "2"], "t": [1, 3]},
            "XETHZUSD": {"c": ["10", "1"], "v": ["0", "0"], "t": [0, 0]},
            "SOLUSD": {"c": ["0", "1"], "v": ["1", "1"], "t": [1, 1]}}}
        prices = exchange_prices.quotes("kraken", ["XXBT", "XETH", "SOL", "ZUSD"], get=lambda url, timeout: data, now_ms=NOW)
        self.assertEqual(set(prices), {"XXBT", "ZUSD"})
        self.assertEqual(prices["XXBT"]["provider"], "Kraken BTC/USD price")
        self.assertEqual(prices["ZUSD"]["provider"], "US dollar cash")

    def test_bybit_rejects_a_stale_reply(self):
        reply = {"retCode": 0, "time": NOW, "result": {"list": [{"symbol": "BTCUSDC", "lastPrice": "7", "volume24h": "2"}]}}
        self.assertEqual(exchange_prices.quotes("bybit", ["BTC"], get=lambda url, timeout: reply, now_ms=NOW)["BTC"]["price"], "7")
        stale = {**reply, "time": NOW - 3_600_001}
        self.assertEqual(exchange_prices.quotes("bybit", ["BTC"], get=lambda url, timeout: stale, now_ms=NOW), {})

    def test_failures_leave_only_stablecoins_and_stablecoins_need_no_request(self):
        def broken(url, timeout):
            raise OSError("fictional outage")
        self.assertEqual(set(exchange_prices.quotes("bybit", ["BTC", "USDC"], get=broken, now_ms=NOW)), {"USDC"})

        def never(url, timeout):
            raise AssertionError("no request expected")
        self.assertEqual(set(exchange_prices.quotes("binance", ["USDC", "USDT"], get=never, now_ms=NOW)), {"USDC", "USDT"})
        with self.assertRaises(ValueError):
            exchange_prices.quotes("other", ["BTC"])

    def test_exchange_point_counts_every_priced_asset_once(self):
        result = {"fetchedAt": "2026-11-10T00:00:00+00:00", "permissionStatus": "exact", "balances": [
            {"asset": "BTC", "free": "0.5", "locked": "0.25"}, {"asset": "USDC", "free": "10", "locked": "0"},
            {"asset": "MYSTERY", "free": "100", "locked": "0"}]}
        prices = {"BTC": {"price": "100", "at": "2026-11-10T00:00:00+00:00", "provider": "Binance BTC/USDC price"},
                  "USDC": {"price": "1", "at": "2026-11-10T00:00:00+00:00", "provider": "USDC counted at $1"}}
        point = performance.exchange_point("binance", result, {}, prices)
        self.assertEqual(point["valueUsd"], "85.00")
        self.assertEqual([line["asset"] for line in point["lines"]], ["BTC", "USDC"])


if __name__ == "__main__":
    unittest.main()
