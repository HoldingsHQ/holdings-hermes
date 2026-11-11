"""Fictional Kraken catalog and OHLC boundaries for the ticker watchlist."""
import unittest
from unittest.mock import patch

from tests.test_market import candle, market


def listed(base, alias, *, status="online", quote="ZUSD", currency="USD"):
    return {"wsname": f"{base}/{currency}", "altname": alias, "status": status, "quote": quote}


class TickerWatchlistTest(unittest.TestCase):
    def test_catalog_only_lists_unique_online_usd_spot_markets(self):
        def fetch(url):
            self.assertEqual(url, market.KRAKEN_CATALOG)
            return {"error": [], "result": {
                "ADAUSD": listed("ADA", "ADAUSD"),
                "XXRPZUSD": listed("XRP", "XRPUSD"),
                "XXBTZUSD": listed("XBT", "XBTUSD"),
                "SOLUSD": listed("SOL", "SOLUSD"),
                "FOOEUR": listed("FOO", "FOOEUR", quote="ZEUR", currency="EUR"),
                "ABCUSD": listed("ABC", "ABCUSD", status="cancel_only"),
                "BADUSD": listed("BAD", "bad/route"),
                "DUPUSD": listed("DUP", "DUPUSD"),
                "XXDUPZUSD": listed("DUP", "DUPUSD"),
            }}
        self.assertEqual(market.read_catalog(fetch)["markets"], {
            "ADA": ("ADAUSD", "ADAUSD"), "XRP": ("XXRPZUSD", "XRPUSD"),
        })

    def test_charts_verify_canonical_exchange_identity(self):
        def fetch(url):
            self.assertIn("pair=XRPUSD", url)
            if "/OHLC?" in url:
                return {"error": [], "result": {"XXRPZUSD": [candle(3600), candle(7200)], "last": 0}}
            return {"error": [], "result": {"XXRPZUSD": {"c": ["1.25"]}}}
        result = market._read_kraken_market("XXRPZUSD", "XRPUSD", "kraken:XRP", fetch, lambda: 7500)
        self.assertEqual(result["market"], "kraken:XRP")
        self.assertEqual(result["lastTrade"], "1.25")
        with self.assertRaises(ValueError):
            market._read_kraken_market("XXRPZUSD", "XRPUSD", "kraken:XRP",
                                      lambda _: {"error": [], "result": {"ADAUSD": [], "last": 0}}, lambda: 7500)

    def test_invalid_or_unlisted_ids_do_not_fetch_candles(self):
        with patch.object(market, "_catalog", return_value={"status": "ready", "markets": {}}), \
                patch.object(market, "_read_kraken_market", side_effect=AssertionError("Unexpected quote request")):
            self.assertEqual(market.watch_snapshot("ADA?evil=1"), {"status": "invalid"})
            self.assertEqual(market.watch_snapshot("ADA"), {"status": "unsupported"})

    def test_failed_queries_are_cached_and_misses_are_bounded(self):
        pairs = {f"A{i}": (f"A{i}USD", f"A{i}USD") for i in range(11)}
        with patch.object(market, "_catalog", return_value={"status": "ready", "markets": pairs}), \
                patch.object(market, "_watch_cache", market.OrderedDict()), \
                patch.object(market, "_watch_fetches", market.deque()), \
                patch.object(market.time, "monotonic", return_value=100), \
                patch.object(market, "_read_kraken_market", side_effect=ValueError) as read:
            for i in range(10):
                self.assertEqual(market.watch_snapshot(f"A{i}"), {"status": "unavailable"})
                self.assertEqual(market.watch_snapshot(f"A{i}"), {"status": "unavailable"})
            self.assertEqual(market.watch_snapshot("A10"), {"status": "rate-limited"})
            self.assertEqual(read.call_count, 10)
