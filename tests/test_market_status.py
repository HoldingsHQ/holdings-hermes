"""Public status parsing and tentative meeting dates with fictional payloads."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

package = ModuleType("holdings_status_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
from holdings_status_tests import market_status as status


class MarketStatusTests(unittest.TestCase):
    def test_calendar_uses_eastern_dates_and_has_both_years(self):
        date = datetime(2026, 12, 10, 1, tzinfo=timezone.utc)
        self.assertEqual(status.upcoming(date)[0], {"start": "2026-12-08", "end": "2026-12-09"})
        self.assertEqual(status.upcoming(date)[1]["start"], "2027-01-26")
        self.assertEqual(status.upcoming(datetime(2028, 1, 3, tzinfo=timezone.utc)), [])

    def test_rss_uses_only_official_fomc_statement_paths(self):
        def item(url):
            return f"<item><title>Federal Reserve issues FOMC statement</title><link>{url}</link>" \
                   "<pubDate>Wed, 28 Oct 2026 14:00:00 EDT</pubDate></item>"

        url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm"
        raw = ("<rss><channel>" + item(url) + item(url.replace("www.federalreserve.gov", "untrusted.invalid"))
               + item(url + "?unexpected=1") + "</channel></rss>").encode()
        [release] = status.statements(raw)
        self.assertEqual(release["source"], url)
        self.assertEqual(release["at"], "2026-10-28T18:00:00+00:00")
        with self.assertRaises(ValueError):
            status.statements(b"<!DOCTYPE rss [<!ENTITY x 'unexpected'>]><rss/>")

    def test_exchange_maintenance_and_unknown_states_are_distinct(self):
        self.assertEqual(status.exchange_status("Kraken", {"result": {"status": "post_only"}, "error": []})[1], "warning")
        self.assertEqual(status.exchange_status("Binance.com", {"status": 1})[1], "warning")
        self.assertEqual(status.exchange_status("Binance.com", {"status": True})[1], "unavailable")
        self.assertEqual(status.exchange_status("Bybit", {"retCode": 0, "result": {"list": []}})[0],
                         "No active notices in the public feed")
        events = [{"state": "ongoing", "env": 1, "title": "Fictional maintenance"}]
        self.assertEqual(status.exchange_status("Bybit", {"retCode": 0, "result": {"list": events}})[1], "warning")
        self.assertEqual(status.exchange_status("Bybit", {"retCode": 403})[1], "unavailable")
