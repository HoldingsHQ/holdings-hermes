"""Stage 12 review fixes: handler argument guard, Bybit reply shape and alert coverage."""
import importlib
import json
import unittest
from unittest.mock import patch

from tests.test_kraken_review import package
from tests.test_sample import plugin

exchange_prices = importlib.import_module(f"{package.__name__}.exchange_prices")
watch = importlib.import_module(f"{package.__name__}.alert_watch")
NOW = 1_800_000_000_000
WALLET_A, WALLET_B = "0x" + "a" * 40, "0x" + "b" * 40


class ReviewRoundTests(unittest.TestCase):
    def test_sample_lookup_rejects_non_object_arguments_with_json(self):
        for args in (None, [], "fees", 3):
            with self.subTest(args=args):
                self.assertIn("error", json.loads(plugin.sample_lookup(args)))

    def test_bybit_reply_with_a_non_object_result_gives_no_prices(self):
        for result in (["unexpected"], "unexpected", 7):
            reply = {"retCode": 0, "time": NOW, "result": result}
            with self.subTest(result=result):
                self.assertEqual(exchange_prices.quotes("bybit", ["BTC", "USDC"], get=lambda url, timeout: reply,
                                                        now_ms=NOW).keys(), {"USDC"})

    def test_history_checks_are_partial_when_a_watched_ethereum_wallet_was_not_read(self):
        report = {"coverage": [{"complete": True}]}
        empty = {"findings": []}
        wallets = [(WALLET_A, 1), (WALLET_B, 1)]
        with patch.object(watch.ethereum_review, "review", return_value=report), \
                patch.object(watch.wallet_security, "outflow_checks", return_value=empty), \
                patch.object(watch.wallet_security, "lookalike_checks", return_value=empty):
            both = watch._history(wallets, [{"status": "ready", "block": 900}] * 2, "fictional-key", {"outflow", "lookalike"})
            one = watch._history(wallets, [{"status": "ready", "block": 900}, {"status": "unavailable"}],
                                 "fictional-key", {"outflow", "lookalike"})
        self.assertEqual({name: status for name, (status, _) in both.items()}, {"outflow": "ready", "lookalike": "ready"})
        self.assertEqual({name: status for name, (status, _) in one.items()}, {"outflow": "partial", "lookalike": "partial"})


if __name__ == "__main__":
    unittest.main()
