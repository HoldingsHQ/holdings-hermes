"""Only the scoped fictional context reaches the model bridge."""
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("holdings_chat_fixture", root / "__init__.py",
                                              submodule_search_locations=[str(root)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
chat = importlib.import_module(spec.name + ".sample_chat")


def request(**overrides):
    return {"source": "kraken", "start": "2026-01-01", "end": "2026-01-01", "record": None,
            "question": "What fees did I pay?", "history": [], **overrides}


class ModelBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_slash_uses_minimal_fictional_projection(self):
        commands, calls = {}, []
        response = '{"text":"The fee was 0.0002 BTC.","citations":["H3"]}'

        async def model(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=response, provider="custom", model="fixture")

        chat.register_sample_chat(SimpleNamespace(llm=SimpleNamespace(acomplete=model),
            register_command=lambda name, handler, **kwargs: commands.update({name: handler})))
        slash = commands["holdings-sample"]
        self.assertIn("short question", await slash("x" * (chat.MAX_QUESTION + 1)))
        reply = await slash("What fees did I pay?")
        self.assertIn("custom / fixture", reply)
        self.assertIn("[H3] csv:kraken:sample:ledger:L-SAMPLE", reply)
        self.assertEqual(set(commands), {"holdings-sample"})
        self.assertIn("Fictional sample", reply)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["purpose"], "holdings.fictional-conversation")
        context = json.loads(calls[0]["messages"][1]["content"])
        self.assertLess(len(context["records"]), len(package.sample_data()["rows"]))
        self.assertNotIn("L-SAMPLE", json.dumps(context))
        self.assertNotIn("observations", json.dumps(context))
        response = '{"text":"Fake claim.","citations":["H99"]}'
        rejected = await slash("What fees did I pay?")
        self.assertIn("did not return a verifiable sample answer", rejected)
        self.assertNotIn("Fake claim", rejected)

    def test_retrieval_and_follow_up_are_scoped(self):
        context, ids, facts = chat.build_context(request())
        self.assertEqual(list(ids), ["H3"])
        self.assertEqual(facts[0]["text"], "Recorded fees in the supplied rows: 0.0002 BTC.")
        self.assertNotIn("observations", json.dumps(context))
        self.assertNotIn("L-SAMPLE", json.dumps(context))
        context, ids, _ = chat.build_context(request(
            question="Was that for a trade?", history=[{"question": "What fees did I pay?", "citations": ["H3"]}]))
        self.assertEqual(list(ids), ["H3"])
        self.assertEqual(context["prior_questions"], ["What fees did I pay?"])
        with self.assertRaises(ValueError):
            chat.build_context(request(source="wallet", history=[{"question": "Earlier fees?", "citations": ["H3"]}]))
        with self.assertRaises(ValueError):
            chat.build_context(request(source="wallet", record="kraken:sample:ledger:L-SAMPLE"))
        context, ids, _ = chat.build_context(request(start="2026-01-02", end=None))
        self.assertEqual((context["records"], ids), ([], {}))

    async def test_command_uses_configured_route_and_validates_citations(self):
        commands = {}
        calls = []
        async def model(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='{"text":"Kraken records this as a trade fee.","citations":["H3"]}',
                                   provider="custom", model="fixture")
        chat.register_sample_chat(SimpleNamespace(llm=SimpleNamespace(acomplete=model),
            register_command=lambda name, handler, **kwargs: commands.update({name: handler})))
        result = await commands["holdings-sample"]("What fees did I pay?")
        self.assertIn("Kraken records this as a trade fee.", result)
        self.assertIn("csv:kraken:sample:ledger:L-SAMPLE", result)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("provider", calls[0])
        self.assertNotIn("model", calls[0])
        self.assertNotIn("profile", calls[0])
        records = json.loads(calls[0]["messages"][1]["content"])["records"]
        text, refs = chat._reply('{"text":"The fee was 0.0002 BTC.","citations":["H3"]}', {"H3"}, records)
        self.assertEqual((text, refs), ("The fee was 0.0002 BTC.", ["H3"]))
        with self.assertRaises(ValueError):
            chat._reply('{"text":"The fee was 0.0002 ETH.","citations":["H3"]}', {"H3"}, records)
        for reply in ('{"text":"The fee was 100 ETH.","citations":["H3"]}',
                      '{"text":"A fee appears.","citations":["H1"]}',
                      '{"text":"A fee appears.","citations":[]}'):
            with self.assertRaises(ValueError):
                chat._reply(reply, {"H3"})

    async def test_provider_failure_is_not_a_model_answer(self):
        commands = {}
        async def fail(**kwargs):
            raise RuntimeError("provider detail is not returned")
        chat.register_sample_chat(SimpleNamespace(llm=SimpleNamespace(acomplete=fail),
            register_command=lambda name, handler, **kwargs: commands.update({name: handler})))
        result = await commands["holdings-sample"]("What fees did I pay?")
        self.assertIn("could not answer", result)
        self.assertNotIn("provider detail", result)
