"""The 0.1.4 security review. Each case was reproduced against the old code first. Fictional data only."""
import asyncio
import importlib
import io
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.test_kraken_review import package

ask = importlib.import_module(package.__name__ + ".ask_chat")
public_chain = importlib.import_module(package.__name__ + ".public_chain")
ROOT = Path(__file__).resolve().parents[1]
SEND = {"id": "sample:ethereum:eth-transfer-1", "label": "Send", "asset": "ETH", "amount": "-0.1",
        "at": "2026-01-01T08:30:00+00:00"}


def body(pack, demo=True, record=None, question="What is my balance?"):
    return json.dumps({"question": question, "record": record, "demo": demo, "history": [], "pack": pack})


def context(reply, private=False):
    calls = []

    async def complete(**kwargs):
        calls.append(kwargs)
        if kwargs["purpose"] == "holdings.ask-plan":
            return {"text": json.dumps({"tools": ["balances", "markets"]})}
        return {"text": json.dumps(reply), "provider": "fixture", "model": "fixture-ask"}
    return SimpleNamespace(llm=SimpleNamespace(acomplete_structured=complete),
                           get_config=lambda key, default=None: private if key == ask.OPT_IN else default), calls


def model_input(calls):
    return next(json.loads(call["input"][0]["text"]) for call in calls if call["purpose"] == "holdings.ask")


class DemoFlagTests(unittest.TestCase):
    """Finding 1. With the private data setting off, the caller's demo flag no longer decides what is sent."""

    def test_non_fixture_records_marked_demo_never_reach_the_model(self):
        private = {"balances": [{"id": "wallet:0:ETH", "label": "Fictional private wallet", "amount": "1.5", "value_usd": 4800}]}
        ctx, calls = context({"text": "You hold 1.5 ETH.", "citations": ["wallet:0:ETH"]})
        self.assertEqual(asyncio.run(ask.answer(ctx, body(private), platform="local"))["status"], "consent-required")
        self.assertEqual(calls, [])

    def test_fixture_ids_carry_bundled_values_not_caller_values(self):
        pack = {"balances": [{"id": "demo-position:0", "label": "Fictional private label", "value_usd": 9_999_999},
                             {"id": "wallet:0:ETH", "label": "Fictional private wallet", "value_usd": 4800}]}
        ctx, calls = context({"text": "Wallet tokens are worth 100250 USD.", "citations": ["demo-position:0"]})
        result = asyncio.run(ask.answer(ctx, body(pack), platform="local"))
        self.assertEqual(result["status"], "model")
        sent = model_input(calls)
        self.assertEqual(sent["workspace"], "Fictional demo data")
        self.assertEqual(sent["tool_results"]["balances"],
                         [{"id": "demo-position:0", "label": "Wallet tokens", "value_usd": 100250, "at": "2026-11-08T17:20:00Z"}])
        self.assertNotIn("Fictional private", json.dumps(sent))

    def test_selected_record_outside_the_fixtures_is_refused(self):
        pack = {"activity": [dict(SEND, id="kraken:fictional-private-row")]}
        ctx, calls = context({"text": "x", "citations": ["kraken:fictional-private-row"]})
        result = asyncio.run(ask.answer(ctx, body(pack, record="kraken:fictional-private-row"), platform="local"))
        self.assertEqual(result["status"], "consent-required")
        self.assertEqual(calls, [])

    def test_review_ids_with_a_source_prefix_map_to_the_bundled_row(self):
        item = ask._fictional("ethereum:fictional-transfer-2")
        self.assertEqual((item["amount"], item["asset"], item["at"]), ("0.490", "ETH", "2026-01-01T09:08:00+00:00"))
        self.assertIsNone(ask._fictional("ethereum:not-a-fixture"))
        self.assertIsNone(ask._fictional("demo-position:99"))

    def test_public_quotes_keep_only_safe_fields(self):
        quote = ask._public_quote({"id": "market:kraken:BTC/USD", "label": "BTC/USD", "price": 64507.0,
                                   "at": "2026-11-11T08:00:00.000Z", "source": "Kraken Spot last trade", "detail": "x"})
        self.assertEqual(set(quote), {"id", "label", "price", "at", "source"})
        self.assertNotIn("label", ask._public_quote({"id": "market:x", "price": 1.0, "label": "Visit claim; now!"}))
        self.assertIsNone(ask._public_quote({"id": "wallet:0", "price": 1.0}))

    def test_private_setting_on_still_forwards_the_users_records(self):
        pack = {"balances": [{"id": "wallet:0:ETH", "label": "Wallet 1 ETH", "amount": "1.5", "value_usd": 4800}]}
        ctx, calls = context({"text": "Wallet 1 holds 1.5 ETH.", "citations": ["wallet:0:ETH"]}, private=True)
        self.assertEqual(asyncio.run(ask.answer(ctx, body(pack, demo=True), platform="local"))["status"], "model")
        self.assertEqual(model_input(calls)["workspace"], "The user's own Holdings data")


class NumberCheckTests(unittest.TestCase):
    """Finding 2. Numbers come from value fields only, direction must match and safety claims fail closed."""

    def check(self, text, items, refs=None):
        return ask.check_answer(json.dumps({"text": text, "citations": refs or [items[0]["id"]]}), items)

    def test_direction_must_match_the_record_sign(self):
        with self.assertRaisesRegex(ValueError, "direction"):
            self.check("You received 0.1 ETH.", [SEND])
        self.check("You sent 0.1 ETH.", [SEND])
        receive = dict(SEND, id="r", amount="0.490")
        with self.assertRaisesRegex(ValueError, "direction"):
            self.check("The wallet sent 0.490 ETH.", [receive])
        self.check("Kraken sent 0.1 ETH and the wallet received 0.490 ETH.", [SEND, receive], [SEND["id"], "r"])

    def test_ids_do_not_vouch_for_numbers(self):
        with self.assertRaisesRegex(ValueError, "Number outside"):
            self.check("You hold 2026 ETH.", [{"id": "record:2026", "asset": "ETH"}])
        with self.assertRaisesRegex(ValueError, "Number outside"):
            self.check("You hold 7 ETH.", [{"id": "a", "asset": "ETH", "related": ["record:7"]}])
        self.check("This happened on 2026-01-01.", [SEND])
        self.check("Aave v3 shows a health factor of 1.86.", [{"id": "p", "label": "Aave v3 on Ethereum", "health": "1.86"}])

    def test_safety_assurances_are_rejected(self):
        for text in ("This token is confirmed as safe.", "The contract is verified and secure.",
                     "Holdings guarantees this transfer.", "This swap was risk-free.", "It is 100% safe."):
            with self.assertRaisesRegex(ValueError, "safety"):
                self.check(text, [SEND])
        self.check("GoPlus flagged this token. Review it before you approve anything.", [SEND])


class LinkFilterTests(unittest.TestCase):
    """Finding 3. Any dotted host name is a link, whatever its ending."""

    def test_hosts_with_any_ending_are_blocked(self):
        for text in ("Visit support.example.dev for help.", "See wallet-help.example.ai now.", "Open example.com today.",
                     "Try claim.example.co.uk instead.", "Go to https://x", "Use www.example now.",
                     "Send to 0x1111111111111111111111111111111111111111."):
            self.assertTrue(ask.LINK.search(text), text)

    def test_ordinary_answers_are_not_links(self):
        for text in ("You sent 0.1 ETH on 2026-01-01.", "Wallet tokens are worth 100,250 USD.", "Aave v3 on Ethereum has 1.86 health.",
                     "The Kraken sample shows 0.02 BTC.", "Uniswap v3 on Base holds 8400 USD."):
            self.assertIsNone(ask.LINK.search(text), text)


class NestingTests(unittest.TestCase):
    """Finding 4. Deep nesting is invalid on every supported Python, never a RecursionError."""

    def test_deep_input_is_invalid(self):
        deep = "[" * 2000 + "]" * 2000
        with self.assertRaises(ValueError):
            ask.parse_request(deep)
        with self.assertRaises(ValueError):
            ask.check_answer(deep, [{"id": "a"}])
        self.assertEqual(asyncio.run(ask.answer(context({})[0], deep, platform="local")), {"status": "invalid"})

    def test_recursion_error_from_the_parser_becomes_invalid(self):
        with patch.object(ask.json, "loads", side_effect=RecursionError):
            with self.assertRaises(ValueError):
                ask.parse_request(body({}))

    def test_brackets_inside_strings_do_not_count(self):
        question = "[" * 200
        parsed = ask.parse_request(json.dumps({"question": question[:200], "record": None, "demo": True, "history": [], "pack": {}}))
        self.assertEqual(parsed["question"], question[:200])


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CooldownAfterPaceTests(unittest.TestCase):
    """Finding 5. A request that waited for its turn checks the 429 pause again before it goes out."""

    def setUp(self):
        public_chain._cooldown.clear()
        public_chain._next_slot.clear()

    tearDown = setUp

    def test_queued_request_stops_when_a_429_lands_during_its_wait(self):
        host = "fictional-rpc.example.test"
        opened = []
        opener = SimpleNamespace(open=lambda request, timeout=8: opened.append(1) or Response(b"{}"))
        public_chain._next_slot[host] = time.monotonic() + 0.3

        def other_read():
            time.sleep(0.1)
            with public_chain._pace_lock:
                public_chain._cooldown[host] = time.monotonic() + 60
        thread = threading.Thread(target=other_read)
        thread.start()
        with self.assertRaisesRegex(OSError, "pausing"):
            public_chain.fetch(f"https://{host}/", opener=opener)
        thread.join()
        self.assertEqual(opened, [])

    def test_request_without_a_pause_still_goes_out(self):
        opened = []
        opener = SimpleNamespace(open=lambda request, timeout=8: opened.append(1) or Response(b'{"ok": true}'))
        self.assertEqual(public_chain.fetch("https://fictional-rpc.example.test/", opener=opener), {"ok": True})
        self.assertEqual(opened, [1])


class DocLinkTests(unittest.TestCase):
    """The privacy doc pointed to a SECURITY.md that the archive does not ship."""

    def test_relative_links_in_shipped_docs_resolve(self):
        for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", doc.read_text(encoding="utf-8")):
                if not re.match(r"[a-z]+:", target):
                    self.assertTrue((doc.parent / target).exists(), f"{doc.name} links to missing {target}")

    def test_readme_names_every_host_the_python_code_calls(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        hosts = set()
        for path in ROOT.glob("*.py"):
            hosts |= set(re.findall(r"https://([a-z0-9.-]+\.[a-z]{2,})[/\"'{]", path.read_text(encoding="utf-8")))
            hosts |= set(re.findall(r'HOST = "([a-z0-9.-]+)"', path.read_text(encoding="utf-8")))
        hosts -= {"token-icons", "nft2-cdn"}
        # Revoke.cash is a review link the user opens, never a request Holdings makes.
        link_only = {"revoke.cash"}
        self.assertTrue(hosts - link_only)
        for host in sorted(hosts - link_only):
            self.assertIn(f"`{host}`", readme, host)
        for host in link_only & hosts:
            self.assertIn(host, readme.lower(), host)
        self.assertIn("`token-icons.s3.amazonaws.com`", readme)
        self.assertIn("`nft2-cdn.alchemy.com`", readme)


if __name__ == "__main__":
    unittest.main()
