"""Public help retrieval must not attach portfolio state or untrusted sources."""
import importlib
import json
from types import SimpleNamespace
import unittest
from tests.test_kraken_review import package

support = importlib.import_module(package.__name__ + ".support")


class SupportTests(unittest.IsolatedAsyncioTestCase):
    def test_guide_retrieval_and_followup_are_bounded(self):
        result = json.loads(support.lookup({"question": "Where do I add my API keys?"}))
        self.assertIn("keys", [item["id"] for item in result["articles"]])
        articles, previous = support.select_articles("Where do I do that?", [
            {"question": "Where do I add my API keys?", "citations": ["keys"]}])
        self.assertEqual(articles[0]["id"], "keys")
        self.assertEqual(len(previous), 1)
        self.assertLessEqual(len(articles), 4)
        self.assertEqual(len({item["id"] for item in support.guide()["articles"]}), len(support.guide()["articles"]))

    async def test_model_receives_help_only_and_cites_known_articles(self):
        calls, commands = [], {}
        async def complete(**kwargs):
            calls.append(kwargs)
            return {"text": json.dumps({"article": "keys"}),
                    "provider": "fixture", "model": "fixture-support"}
        ctx = SimpleNamespace(llm=SimpleNamespace(acomplete_structured=complete),
            register_command=lambda name, handler, **kwargs: commands.update({name: handler}), register_tool=lambda **kwargs: None)
        support.register(ctx)
        handler = commands["holdings-help-model"]
        payload = {"question": "How do I add API keys?", "history": [], "article": "keys"}
        result = json.loads(await handler(json.dumps(payload), platform="local"))
        self.assertEqual(result["status"], "model")
        self.assertEqual(result["citations"], ["keys"])
        self.assertEqual(result["text"], next(a["answer"] for a in support.guide()["articles"] if a["id"] == "keys"))
        context = json.loads(calls[0]["input"][0]["text"])
        self.assertEqual(set(context), {"question", "prior_questions", "articles"})
        self.assertLessEqual(len(context["articles"]), 4)
        for invalid in ({**payload, "rows": [{"private": "not sent"}]},
                        {**payload, "question": "api_key=" + "FICTIONAL" * 8},
                        {**payload, "history": [{"question": "why?", "citations": ["invented"]}]},
                        {**payload, "question": "0x" + "1" * 40}):
            self.assertEqual(json.loads(await handler(json.dumps(invalid), platform="local"))["status"], "invalid")
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(await handler(json.dumps(payload), platform="telegram"))["status"], "local-required")

    async def test_provider_error_or_wrong_citation_is_an_explicit_guide_fallback(self):
        commands = {}
        for response in (None, {"text": '{"article":"unknown"}'}, {"text": '{"article":"keys","text":"Invented setup steps"}'}):
            async def complete(**kwargs):
                if response is None:
                    raise RuntimeError("fixture provider unavailable")
                return response
            support.register(SimpleNamespace(llm=SimpleNamespace(acomplete_structured=complete),
                register_command=lambda name, handler, **kwargs: commands.update({name: handler}), register_tool=lambda **kwargs: None))
            result = json.loads(await commands["holdings-help-model"](json.dumps({
                "question": "How do I cancel?", "history": [], "article": "model-errors"}), platform="local"))
            self.assertEqual(result["status"], "guide")
            self.assertEqual(result["reason"], "provider-error" if response is None else "unverified-answer")
            self.assertEqual(result["citations"], ["model-errors"])
            self.assertNotIn("invented", result["text"])


if __name__ == "__main__":
    unittest.main()
