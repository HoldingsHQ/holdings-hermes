"""Saved alert watch rules and deterministic dedupe without provider calls."""
from contextlib import nullcontext
from copy import deepcopy
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_sample_chat import package

watch = importlib.import_module(f"{package.__name__}.alert_watch")


class AlertWatchTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}

    def action(self, action, **data):
        def store(mode):
            return deepcopy(self.saved.get(mode)), lambda value: self.saved.update({mode: deepcopy(value)})

        return watch.request({"action": action, **data}, open_store=store,
                             context=lambda: (None, None, None), lock=lambda home: nullcontext(),
                             evaluate_live=self.evaluate, get_secret=lambda name: None)

    def evaluate(self, state, **kwargs):
        self.assertIn("rules", state)
        return {"price": "ready"}, [watch._alert("price", "rising", "Price move", "Fixture", "2026-11-09", ["quote"])]

    def test_live_rules_and_dedupe_survive_fresh_reads(self):
        default = self.action("read")
        self.assertEqual([name for name, rule in default["rules"].items() if rule["enabled"]], ["security", "hacks"])
        self.assertEqual(self.action("rule", rule="price", enabled=True, muted=False, threshold=0)["status"], "unavailable")
        self.action("rule", rule="price", enabled=True, muted=True, threshold=4.2)
        address = "0x" + "1a" * 20
        selected = self.action("wallets", wallets=[{"address": address, "chainId": 1}])
        self.assertEqual(selected["walletCount"], 1)
        self.assertNotIn(address, str(selected))
        first, second = self.action("run"), self.action("run")
        self.assertEqual(first["new"], [])
        self.assertEqual(len(first["alerts"]), 1)
        self.assertEqual(first["alerts"], second["alerts"])
        self.assertTrue(self.action("read")["rules"]["price"]["muted"])
        self.assertEqual(len(self.action("read")["alerts"]), 1)

    def test_condition_can_rearm_only_after_a_successful_clear(self):
        state = watch.default_state(watch.MODE)
        item = self.evaluate(state)[1][0]
        state["checkedAt"] = "2026-11-09T01:00:00Z"
        self.assertEqual(len(watch.ingest(state, [item], {"price": "ready"})), 1)
        watch.ingest(state, [], {"price": "unavailable"})
        self.assertEqual(watch.ingest(state, [item], {"price": "ready"}), [])
        watch.ingest(state, [], {"price": "ready"})
        state["checkedAt"] = "2026-11-09T01:15:00Z"
        self.assertEqual(len(watch.ingest(state, [item], {"price": "ready"})), 1)
        self.assertEqual(len(state["seen"]), 2)

    def test_disabled_checks_do_not_read_providers(self):
        state = watch.default_state(watch.MODE)
        for rule in state["rules"].values():
            rule["enabled"] = False
        with patch.object(watch, "_balance_check") as balances, patch.object(watch, "_exchange") as exchange:
            self.assertEqual(watch.evaluate(state, get_secret=lambda name: self.fail("unexpected key")), ({}, []))
            balances.assert_not_called()
            exchange.assert_not_called()

    def test_concurrent_run_is_rejected_and_releases_file_lock(self):
        with TemporaryDirectory() as home:
            with watch.state_lock(home):
                with self.assertRaises(BlockingIOError):
                    with watch.state_lock(home):
                        self.fail("second process obtained the lock")
                self.assertEqual((Path(home) / "plugin-data/holdings/.alerts-watch.lock").stat().st_mode & 0o777, 0o600)
            with watch.state_lock(home):
                pass

    def test_poll_requires_opt_in_and_read_state_persists(self):
        self.assertIsNone(self.action("poll")["checkedAt"])
        self.action("desktop", enabled=True)
        checked = self.action("poll")
        self.assertEqual(checked["unread"], 1)
        self.assertEqual(self.action("poll")["new"], ())
        identity = checked["alerts"][0]["id"]
        self.assertEqual(self.action("mark-read", id=identity)["unread"], 0)
        self.assertTrue(self.action("read")["alerts"][0]["read"])
        self.assertEqual(self.action("read")["unread"], 0)

    def test_fixture_is_stateless_and_cron_sample_is_disabled(self):
        def forbidden(*args, **kwargs):
            self.fail("Fictional watch touched storage or provider")
        def call(action, **body):
            return watch.request({"action": action, **body}, allow_cron=True, open_store=forbidden,
                                 context=forbidden, lock=forbidden, evaluate_live=forbidden)
        sample = call("fixture")
        self.assertEqual(sample["status"], "ready")
        self.assertTrue(sample["alerts"][0]["fictional"])
        self.assertEqual(call("fixture"), sample)
        self.assertEqual(call("cron-sample")["status"], "invalid")
        self.assertEqual(call("mark-read", id=sample["alerts"][0]["id"], fictional=True)["status"], "invalid")
        self.assertEqual(self.action("read")["alerts"], [])

    def test_scheduled_watch_reports_a_failed_or_incomplete_check(self):
        schedule = importlib.import_module(f"{package.__name__}.alert_schedule")
        self.assertIn("could not check", schedule.render({"status": "key-source-unavailable"}))
        result = watch.request({"action": "cron"}, allow_cron=True,
                               open_store=lambda mode: (None, lambda state: None),
                               context=lambda: (None, None, None), lock=lambda home: nullcontext(),
                               get_secret=lambda name: None,
                               evaluate_live=lambda state, **kwargs: ({"security": "partial"}, []))
        self.assertEqual(result["newCount"], 0)
        self.assertTrue(result["checksIncomplete"])
        self.assertIn("did not finish", schedule.render(result))

    def test_unknown_saved_rules_are_dropped(self):
        state = watch.default_state(watch.MODE)
        state["rules"]["depeg"] = {"enabled": True, "muted": False, "threshold": 0.5}
        state["rules"]["obsolete"] = {"enabled": True, "muted": False, "threshold": None}
        state["conditions"] = {"depeg": ["old-finding"], "price": []}
        self.saved[watch.MODE] = state
        result = self.action("read")
        self.assertEqual(result["status"], "ready")
        self.assertNotIn("depeg", result["rules"])
        self.assertNotIn("obsolete", result["rules"])
        self.assertIn("security", result["rules"])
        self.assertIn("price", result["rules"])
        self.assertEqual(result["rules"]["security"]["enabled"], True)
        self.assertNotIn("depeg", self.saved[watch.MODE]["rules"])
        self.assertNotIn("depeg", self.saved[watch.MODE]["conditions"])
        self.assertEqual(self.action("rule", rule="depeg", enabled=True, muted=False, threshold=0.5)["status"], "unavailable")
