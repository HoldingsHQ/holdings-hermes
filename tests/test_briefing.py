"""Briefing alerts use fictional profile and quote evidence."""
import importlib
import unittest
from datetime import datetime, timezone
from decimal import Decimal

from tests.test_kraken_review import package

briefing = importlib.import_module(f"{package.__name__}.briefing")
market = importlib.import_module(f"{package.__name__}.market")
NOW = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)


class BriefingTests(unittest.TestCase):
    def test_alerts_are_scoped_dated_and_exclude_raw_amounts(self):
        at = "2026-11-09T08:00:00+00:00"
        stale = "2026-11-07T09:00:00+00:00"
        before = "2026-11-08T09:10:00+00:00"
        line = {"identity": "fictional", "units": "12.345678", "price": "20000.123", "provider": "Kraken BTC/USD"}
        entry = {"source": "Kraken Spot", "at": at, "permissions": "broader", "lines": [line],
                 "complete": True, "observed": 1}
        streams = {"k": [{**entry, "at": before, "permissions": "read-only"}, entry],
                   "b": [{**entry, "source": "Binance Spot", "at": stale}],
                   "w": [{**entry, "source": "Public wallets", "observed": 4, "permissions": None}]}
        attention = {"transfers": {"at": at, "items": {"opaque": at, "old": "2026-10-01T00:00:00+00:00"}}}
        results = briefing.risk_items(streams, attention, {"status": "ready", "price": "0.99", "closedAt": at},
                                      now=NOW, threshold=Decimal(40))
        self.assertEqual({row["name"] for row in results}, {"Stale source", "Since the earlier refresh",
            "Key permissions", "Concentration", "Stablecoin price check", "Needs attention", "Coverage"})
        self.assertEqual(sum(row["name"] == "Concentration" for row in results), 1)
        self.assertTrue(all(row["source"] for row in results))
        self.assertIn("newly reported", str(results))
        self.assertNotIn("12.345678", str(results))
        self.assertNotIn("opaque", str(results))

    def test_attention_tracks_open_and_reversed_decisions_without_original_rows(self):
        state = {}
        def open_store(mode):
            return state or None, state.update
        item = {"id": "1" * 64, "status": "pending", "sourceRecords": ["untrusted-input"]}
        for action in ({"items": [item]}, {"item": {**item, "status": "accepted"}}, {"item": {**item, "status": "undone"}}):
            briefing.note_attention("transfers", action, now=NOW, open_store=open_store)
            self.assertEqual(bool(state["attention"]["transfers"]["items"]), "accepted" not in str(action))
            self.assertNotIn("untrusted-input", str(state))

    def test_fictional_preview_skips_secrets_and_network(self):
        def unexpected(*args):
            self.fail("Sample must not read protected state or market providers")
        report = briefing.preview(sample=True, open_store=unexpected, quote=unexpected, now=NOW)
        self.assertTrue(report["fictional"])
        self.assertTrue(all(item["source"] for item in report["items"]))

    def test_stablecoin_read_checks_exact_market_and_completed_hour(self):
        hour = int(NOW.timestamp())
        row = [hour - 3600, "1", "1", "0.97", "0.99123", "1", "2", 3]
        sample = {"error": [], "result": {market.STABLE_MARKET: [row, [hour, *row[1:]]], "last": 0}}
        quote = market.read_stable_market(lambda _: sample, lambda: hour + 1)
        self.assertEqual(quote["price"], "0.99123")
        with self.assertRaises(ValueError):
            market.read_stable_market(lambda _: sample, lambda: hour + 7201)

    def test_five_weekly_sections_use_bounded_evidence_and_redact_delivery(self):
        report = briefing.preview(sample=True, now=NOW)
        rows = {row["name"]: row for row in report["items"]}
        self.assertIn("+240.00 USD", rows["Value change"]["text"])
        self.assertIn("+4.00%", rows["Top movers"]["text"])
        self.assertIn("USDC", rows["New positions"]["text"])
        self.assertIn("sample:peg-warning", rows["Alerts"]["source"])
        self.assertIn("sample:wallet-security:token", rows["Security findings"]["source"])
        delivery = briefing.scheduled_text(report)
        for name in ("Value change", "Top movers", "New positions", "Alerts", "Security findings"):
            self.assertIn(name, delivery)
        for detail in ("240.00", "USDC", "WETH", "sample:", "2026-"):
            self.assertNotIn(detail, delivery)

    def test_legacy_snapshots_and_outside_window_do_not_invent_holdings_or_alerts(self):
        new = "2026-11-09T08:00:00+00:00"
        old = "2026-11-01T00:00:00+00:00"
        point = {"source": "Public wallets", "at": new, "lines": [], "observed": 0, "complete": True}
        data = {"history": [{**point, "at": old}, point]}
        alerts = [{"at": old, "rule": "outflow", "id": "outside"}, {"at": new, "rule": "security", "id": "sample", "fictional": True}]
        rows = {row["name"]: row for row in briefing._weekly_rows(data, alerts, NOW)}
        self.assertTrue(all(not row["available"] for row in rows.values()))
        self.assertIn("Refresh the same supported scope twice", rows["New positions"]["text"])
        self.assertNotIn("outside", str(rows))
