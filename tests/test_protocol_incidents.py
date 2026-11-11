"""DefiLlama incident normalization uses public-shaped fictional responses."""
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

package = ModuleType("holdings_incident_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
from holdings_incident_tests import protocol_incidents as feed


class ProtocolIncidentTests(unittest.TestCase):
    def test_dated_feed_normalizes_chains_and_omits_untrusted_urls_and_loss_figures(self):
        now = 1780000000
        row = {"name": "Fictional\nProtocol", "date": now - 3, "chain": ["Base", "Ethereum", "Sui"],
               "technique": "Fictional incident", "source": "https://unsafe.invalid", "amount": 9}
        report = feed.recent([row, row, {**row, "date": now + 1}, {**row, "date": now - feed.WINDOW - 1}], now)
        [item] = report["incidents"]
        self.assertEqual(item["name"], "FictionalProtocol")
        self.assertEqual(item["chainIds"], [8453, 1])
        self.assertNotIn("source", item)
        self.assertNotIn("amount", item)
        self.assertFalse(report["limited"])

    def test_cache_and_provider_failure(self):
        with patch.object(feed, "_cache", None), patch.object(feed, "fetch", return_value=[]) as fetch:
            first = feed.snapshot()
            self.assertEqual(first["status"], "ready")
            self.assertEqual(feed.snapshot(), first)
            fetch.assert_called_once_with("https://api.llama.fi/hacks", timeout=6)
        with patch.object(feed, "_cache", None), patch.object(feed, "fetch", return_value={}):
            self.assertEqual(feed.snapshot(), {"status": "unavailable"})
