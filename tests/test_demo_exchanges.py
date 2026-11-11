"""Demo Ask about Binance and Bybit uses fictional rows built here from the bundled fixtures. Fictional data only."""
import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_kraken_review import package

ask = importlib.import_module(package.__name__ + ".ask_chat")
demo = importlib.import_module(package.__name__ + ".demo_exchanges")
ROOT = Path(__file__).resolve().parents[1]
BYBIT_SELL = "bybit-csv:bybit-global:csv:spot:SOLUSDT:9000000000000000002"
BINANCE_BUY = "binance-spot:binance-global:review:spot:ETHUSDT:91000001"


def body(pack, demo_flag=True, question="What fees did I pay on Bybit?", record=None):
    return json.dumps({"question": question, "record": record, "demo": demo_flag, "history": [], "pack": pack})


def context(reply, private=False):
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        if kwargs["purpose"] == "holdings.ask-plan":
            return {"text": json.dumps({"tools": ["activity"]})}
        return {"text": json.dumps(reply), "provider": "fixture", "model": "fixture-ask"}
    return SimpleNamespace(llm=SimpleNamespace(acomplete_structured=complete),
                           get_config=lambda key, default=None: private if key == ask.OPT_IN else default), calls


def sent(calls):
    return next(json.loads(call["input"][0]["text"]) for call in calls if call["purpose"] == "holdings.ask")


class BundledSnapshotTests(unittest.TestCase):
    def test_snapshots_come_from_the_fixture_files_without_a_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            demo._built.cache_clear()
            snapshots = demo.snapshots()
        self.assertEqual(set(snapshots), {"binance-spot", "bybit-csv"})
        self.assertEqual(len(snapshots["binance-spot"]["rows"]), 3)
        self.assertEqual(len(snapshots["bybit-csv"]["rows"]), 2)
        self.assertEqual(snapshots["bybit-csv"]["coverage"][0]["gap"], "file-only")
        snapshots["bybit-csv"]["rows"].clear()
        self.assertEqual(len(demo.snapshots()["bybit-csv"]["rows"]), 2)

    def test_sample_route_serves_the_bundled_snapshots(self):
        spec = importlib.util.spec_from_file_location("holdings_dashboard_demo_probe", ROOT / "dashboard" / "plugin_api.py")
        dashboard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dashboard)
        sample = dashboard.sample()
        self.assertEqual(sample["exchange_demo"]["bybit-csv"]["mode"], "bybit-csv")
        self.assertEqual([row["id"] for row in sample["exchange_demo"]["binance-spot"]["rows"]],
                         [row["id"] for row in demo.snapshots()["binance-spot"]["rows"]])


class DemoExchangeAskTests(unittest.TestCase):
    def test_demo_exchange_question_reaches_the_model_with_bundled_values(self):
        pack = {"activity": [{"id": BYBIT_SELL, "label": "Fictional caller label", "amount": "9.9", "kind": "sell"},
                             {"id": BINANCE_BUY, "label": "Buy", "amount": "0.025", "kind": "buy"}]}
        ctx, calls = context({"text": "The Bybit SOL sale paid a 0.0300 USDT fee.", "citations": [BYBIT_SELL]})
        result = asyncio.run(ask.answer(ctx, body(pack), platform="local"))
        self.assertEqual(result["status"], "model")
        model = sent(calls)
        self.assertEqual(model["workspace"], "Fictional demo data")
        rows = {row["id"]: row for row in model["tool_results"]["activity"]}
        self.assertEqual((rows[BYBIT_SELL]["amount"], rows[BYBIT_SELL]["fee"], rows[BYBIT_SELL]["source"]),
                         ("0.200", "0.0300 USDT", "Bybit Spot CSV"))
        self.assertEqual(rows[BINANCE_BUY]["fee"], "0.00012345 BNB")
        self.assertNotIn("Fictional caller label", json.dumps(model))

    def test_trade_answers_can_say_spent_or_sold(self):
        items = [ask._fictional(BINANCE_BUY)]
        ask.check_answer(json.dumps({"text": "You spent 50 USDT on 0.025 ETH.", "citations": [BINANCE_BUY]}), items)

    def test_rows_that_are_not_bundled_stay_private(self):
        foreign = [{"id": "bybit-csv:bybit-global:csv:spot:ETHUSDT:1234", "label": "Buy", "amount": "1.5", "kind": "buy"},
                   {"id": "binance-spot:binance-global:review:spot:ETHUSDT:99", "label": "Sell", "amount": "2", "kind": "sell"}]
        ctx, calls = context({"text": "You bought 1.5 ETH.", "citations": [foreign[0]["id"]]})
        self.assertEqual(asyncio.run(ask.answer(ctx, body({"activity": foreign}), platform="local"))["status"], "consent-required")
        self.assertEqual(calls, [])
        mixed = {"activity": [*foreign, {"id": BYBIT_SELL, "amount": "0.200", "kind": "sell"}]}
        ctx, calls = context({"text": "The Bybit sale was 0.200 SOL.", "citations": [BYBIT_SELL]})
        self.assertEqual(asyncio.run(ask.answer(ctx, body(mixed), platform="local"))["status"], "model")
        self.assertEqual([row["id"] for row in sent(calls)["tool_results"]["activity"]], [BYBIT_SELL])

    def test_bundled_rows_without_the_demo_flag_still_need_the_setting(self):
        ctx, calls = context({"text": "x", "citations": [BYBIT_SELL]})
        pack = {"activity": [{"id": BYBIT_SELL, "amount": "0.200"}]}
        self.assertEqual(asyncio.run(ask.answer(ctx, body(pack, demo_flag=False), platform="local"))["status"], "consent-required")
        self.assertEqual(calls, [])

    def test_with_the_setting_on_caller_rows_are_the_users_own_data(self):
        pack = {"activity": [{"id": "bybit-csv:bybit-global:csv:spot:ETHUSDT:1234", "label": "Buy", "amount": "1.5", "kind": "buy"}]}
        ctx, calls = context({"text": "You bought 1.5 ETH.", "citations": [pack["activity"][0]["id"]]}, private=True)
        self.assertEqual(asyncio.run(ask.answer(ctx, body(pack), platform="local"))["status"], "model")
        self.assertEqual(sent(calls)["workspace"], "The user's own Holdings data")


if __name__ == "__main__":
    unittest.main()
