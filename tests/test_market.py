import importlib
import unittest

from tests.test_kraken_review import package

market = importlib.import_module(f"{package.__name__}.market")
HOUR = 3600


def candle(at, close="100.125"):
    return [at, "100.00", "101.50", "99.9", close, "100", "1", 12]


class MarketTests(unittest.TestCase):
    def test_exact_market_bounded_closed_candles_and_ticker(self):
        rows = [candle(at * HOUR) for at in range(1, 59)] + [candle(59 * HOUR, "999")]
        calls = []

        def fetch(url):
            calls.append(url)
            if url == market.OHLC:
                return {"error": [], "result": {market.MARKET: rows, "last": 0}}
            return {"error": [], "result": {market.MARKET: {"c": ["100.12340", "1"]}}}

        snapshot = market.read_market(fetch, lambda: 59 * HOUR + 100)
        self.assertEqual(calls, [market.OHLC, market.TICKER])
        self.assertEqual(snapshot["market"], "XXBTZUSD")
        self.assertEqual(len(snapshot["candles"]), 48)
        self.assertEqual(snapshot["candles"][-1][-1], "100.125")
        self.assertEqual(snapshot["lastTrade"], "100.12340")
        self.assertFalse(snapshot["stale"])

    def test_sol_and_bnb_bind_exact_public_market_and_keep_decimal_precision(self):
        for identity in ("SOLUSD", "BNBUSD"):
            calls = []
            def fetch(url):
                calls.append(url)
                self.assertIn("pair=" + identity, url)
                if "/OHLC?" in url:
                    return {"error": [], "result": {identity: [candle(HOUR), candle(2 * HOUR)], "last": 0}}
                return {"error": [], "result": {identity: {"c": ["100.123456789012345678", "1"]}}}
            data = market.read_market(fetch, lambda: 2 * HOUR + 100, market=identity)
            self.assertEqual(data["market"], identity)
            self.assertEqual(data["lastTrade"], "100.123456789012345678")
            self.assertEqual(len(calls), 2)
            with self.assertRaises(ValueError):
                market.read_market(lambda _: {"error": [], "result": {"XXBTZUSD": [], "last": 0}}, market=identity)
        with self.assertRaises(KeyError):
            market.read_market(lambda _: self.fail("Unsupported market made a request"), market="untrusted")

    def test_wrong_market_rejected_and_stale_candle_does_not_require_ticker(self):
        wrong = {"error": [], "result": {"wrong": [], "last": 0}}
        with self.assertRaises(ValueError):
            market.read_market(lambda _: wrong, lambda: 10 * HOUR)

        def fetch(url):
            if url == market.TICKER:
                raise TimeoutError
            return {"error": [], "result": {market.MARKET: [candle(HOUR), candle(2 * HOUR)], "last": 0}}

        snapshot = market.read_market(fetch, lambda: 10 * HOUR)
        self.assertTrue(snapshot["stale"])
        self.assertIsNone(snapshot["lastTrade"])


class PoolMarketTests(unittest.TestCase):
    def payload(self, rows):
        return {"meta": {"base": {"address": market.WETH}, "quote": {"address": market.USDC}},
                "data": {"attributes": {"ohlcv_list": rows}}}

    def candle(self, hour):
        return [hour * HOUR, "100", "102", "99", "101.0023456", "1234.005"]

    def test_exact_pool_request_completed_candles_and_exact_volume(self):
        def fetch(url):
            self.assertEqual(url, market.POOL_OHLC)
            self.assertIn(f"/networks/eth/pools/{market.POOL}/ohlcv/hour?", url)
            self.assertIn(f"currency=usd&token={market.WETH}", url)
            return self.payload([self.candle(hour) for hour in range(49, 0, -1)])

        snapshot = market.read_pool_market(fetch, lambda: 49 * HOUR + 4)
        self.assertEqual(snapshot["market"], f"eip155:1:{market.POOL}")
        self.assertEqual(len(snapshot["candles"]), 48)
        self.assertEqual(snapshot["candles"][-1], [
            market._utc(48 * HOUR), "100", "102", "99", "101.0023456", "1234.005"])
        self.assertEqual(snapshot["closedAt"], market._utc(49 * HOUR))
        self.assertFalse(snapshot["stale"])

    def test_market_changes_require_exact_24_hour_and_7_day_candles(self):
        rows = [self.candle(hour) for hour in range(170, 0, -1)]
        data = market.read_pool_market(lambda _: self.payload(rows), lambda: 170 * HOUR + 1)
        self.assertEqual(data["marketChanges"], {"24h": "0.0000", "7d": "0.0000"})
        data = market.read_pool_market(lambda _: self.payload(rows[:-1]), lambda: 170 * HOUR + 1)
        self.assertEqual(data["marketChanges"], {"24h": "0.0000", "7d": None})

    def test_stale_missing_wrong_tokens_or_invalid_candles(self):
        stale = self.payload([self.candle(8), self.candle(1)])
        snapshot = market.read_pool_market(lambda _: stale, lambda: 11 * HOUR)
        self.assertTrue(snapshot["stale"])
        self.assertEqual(len(snapshot["candles"]), 2)
        wrong = self.payload([self.candle(1)])
        wrong["meta"]["base"]["address"] = market.USDC
        for payload in (wrong, self.payload([]), self.payload([self.candle(11)]),
                        self.payload([self.candle(8), self.candle(8)]),
                        self.payload([self.candle(8)[:-1] + ["-1"]])):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                market.read_pool_market(lambda _: payload, lambda: 11 * HOUR)

    def test_rate_limit_becomes_a_throttled_unavailable_result(self):
        from io import BytesIO
        from urllib.error import HTTPError
        calls = []
        failure = HTTPError(market.POOL_OHLC, 429, "rate limited", None, BytesIO())
        self.addCleanup(failure.close)

        def read():
            calls.append(True)
            raise failure

        snapshot = market._throttled(read)
        self.assertEqual(snapshot(), {"status": "unavailable"})
        self.assertEqual(snapshot(), {"status": "unavailable"})
        self.assertEqual(calls, [True])
