"""Alert watch polls every 5 minutes and each rule runs only when it is due. No provider calls."""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
import importlib
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tests.test_sample_chat import package

watch = importlib.import_module(f"{package.__name__}.alert_watch")
public_chain = importlib.import_module(f"{package.__name__}.public_chain")

START = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
ALL = {"outflow", "security", "lookalike", "price", "exchange", "hacks"}


def all_enabled():
    state = watch.default_state(watch.MODE)
    for config in state["rules"].values():
        config["enabled"] = True
    state["rules"]["price"]["threshold"] = 5
    state["wallets"] = [("0x" + "1a" * 20, 1)]
    return state


class Calls:
    """Stand-in scans that record which rules read a provider."""

    def __init__(self):
        self.ran = []

    def __enter__(self):
        self.stack = ExitStack()
        scan = lambda name: lambda *args, **kwargs: (self.ran.append(name), ("ready", []))[1]
        for name in ("security", "hacks", "price", "exchange"):
            self.stack.enter_context(patch.object(watch, f"_{name}", scan(name)))
        self.stack.enter_context(patch.object(watch, "_balance_check", lambda wallets: (self.ran.append("balances"), [])[1]))

        def history(wallets, balances, key, wanted):
            self.ran.extend(sorted(wanted))
            return {name: ("ready", []) for name in wanted}
        self.stack.enter_context(patch.object(watch, "_history", history))
        return self

    def __exit__(self, *exc):
        self.stack.close()


class IntervalTests(unittest.TestCase):
    def test_the_poll_and_each_rule_interval_match_the_approved_schedule(self):
        self.assertEqual(watch.POLL_SECONDS, 300)
        self.assertEqual(watch.RULE_INTERVALS, {"outflow": 300, "security": 900, "lookalike": 900,
                                                "price": 900, "exchange": 900, "hacks": 900})
        self.assertEqual(set(watch.RULE_INTERVALS), set(watch.RULES))
        self.assertEqual(watch.HACKS_REFRESH, 3 * 3600)
        self.assertEqual(watch.HACKS_RETRY, 15 * 60)

    def test_rules_skip_until_due_across_an_hour_of_five_minute_polls(self):
        state = all_enabled()
        runs = {}
        with Calls() as calls:
            for poll in range(13):
                calls.ran.clear()
                checks, _ = watch.evaluate(state, get_secret=lambda name: None, now=START + timedelta(minutes=5 * poll))
                self.assertEqual(set(checks), {name for name in calls.ran if name != "balances"})
                runs[poll] = set(checks)
        self.assertEqual(runs[0], ALL)
        for poll in (1, 2, 4, 5, 7, 8, 10, 11):
            self.assertEqual(runs[poll], {"outflow"}, poll)
        for poll in (3, 6, 9, 12):
            self.assertEqual(runs[poll], ALL, poll)
        # Per hour: outflow 12 times, the others 4 times each.
        self.assertEqual(sum("outflow" in runs[p] for p in range(12)), 12)
        self.assertEqual(sum("security" in runs[p] for p in range(12)), 4)

    def test_a_slightly_early_poll_still_counts_as_due(self):
        state = all_enabled()
        with Calls():
            watch.evaluate(state, get_secret=lambda name: None, now=START)
            checks, _ = watch.evaluate(state, get_secret=lambda name: None, now=START + timedelta(seconds=297))
        self.assertEqual(set(checks), {"outflow"})

    def test_balances_are_read_only_when_a_due_rule_needs_them(self):
        state = all_enabled()
        for name in ("security", "outflow", "price", "lookalike"):
            state["rules"][name]["enabled"] = False
        with Calls() as calls:
            watch.evaluate(state, get_secret=lambda name: None, now=START)
        self.assertNotIn("balances", calls.ran)

    def test_disabled_rules_never_run_and_saved_run_times_are_validated(self):
        state = all_enabled()
        state["rules"]["exchange"]["enabled"] = False
        self.assertNotIn("exchange", watch.due_rules(state, START))
        state["ruleRuns"] = {"outflow": START.isoformat(), "depeg": START.isoformat(), "price": "soon", "hacks": 4}
        cleaned = watch.normalize_state(state)["ruleRuns"]
        self.assertEqual(cleaned, {"outflow": START.isoformat()})


class RequestTests(unittest.TestCase):
    def setUp(self):
        state = all_enabled()
        state["desktopPoll"] = True
        self.saved = {watch.MODE: state}
        self.calls = []

    def evaluate(self, state, **kwargs):
        due = watch.due_rules(state)
        self.calls.append(due)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["ruleRuns"] = {**state.get("ruleRuns", {}), **{name: stamp for name in due}}
        return {name: "ready" for name in due}, []

    def action(self, action, **data):
        def store(mode):
            return deepcopy(self.saved.get(mode)), lambda value: self.saved.update({mode: deepcopy(value)})
        return watch.request({"action": action, **data}, open_store=store, context=lambda: (None, None, None),
                             lock=lambda home: nullcontext(), evaluate_live=self.evaluate, get_secret=lambda name: None)

    def test_run_forces_every_rule_and_a_poll_keeps_the_skipped_rule_status(self):
        self.action("run")
        self.assertEqual(self.calls[-1], ALL)
        recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(timespec="seconds")
        self.saved[watch.MODE]["ruleRuns"] = {name: recent for name in ALL}
        self.saved[watch.MODE]["ruleRuns"]["outflow"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(timespec="seconds")
        self.saved[watch.MODE]["checkedAt"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(timespec="seconds")
        view = self.action("poll")
        self.assertEqual(self.calls[-1], {"outflow"})
        self.assertEqual(set(view["checks"]), ALL)
        self.assertEqual(self.action("poll")["status"], "ready")
        self.assertEqual(len(self.calls), 2, "a poll inside 5 minutes of the last check is skipped")
        self.action("run")
        self.assertEqual(self.calls[-1], ALL)

    def test_changing_the_watched_wallets_makes_every_rule_due_again(self):
        self.action("run")
        self.action("wallets", wallets=[{"address": "0x" + "2b" * 20, "chainId": 8453}])
        self.assertEqual(self.saved[watch.MODE]["ruleRuns"], {})


class Opener:
    def __init__(self, codes, headers=None):
        self.codes, self.headers, self.sent = list(codes), headers or {}, 0

    def open(self, request, timeout):
        self.sent += 1
        code = self.codes.pop(0)
        message = Message()
        for key, value in self.headers.items():
            message[key] = value
        raise HTTPError(request.full_url, code, "fixture", message, None)


class RateLimitBackoffTests(unittest.TestCase):
    URL = "https://ratelimit.fixture.invalid/rpc"

    def setUp(self):
        public_chain._cooldown.clear()
        self.addCleanup(public_chain._cooldown.clear)
        self.clock = [1000.0]

    def fetch(self, opener):
        return public_chain.fetch(self.URL, {"id": 1}, opener=opener, clock=lambda: self.clock[0])

    def test_a_429_pauses_that_host_and_later_calls_fail_without_a_request(self):
        opener = Opener([429])
        with patch.object(public_chain, "pace", lambda url: None):
            with self.assertRaisesRegex(OSError, "request failed"):
                self.fetch(opener)
            for _ in range(20):
                with self.assertRaisesRegex(OSError, "pausing after a rate limit"):
                    self.fetch(opener)
        self.assertEqual(opener.sent, 1)
        self.assertTrue(public_chain.rate_limited(self.URL, clock=lambda: self.clock[0] + 59))
        self.assertFalse(public_chain.rate_limited(self.URL, clock=lambda: self.clock[0] + 60))
        self.assertFalse(public_chain.rate_limited("https://other.fixture.invalid/", clock=lambda: self.clock[0]))

    def test_retry_after_is_honored_inside_bounds(self):
        self.assertEqual(public_chain._retry_after("120"), 120)
        self.assertEqual(public_chain._retry_after("0"), 5)
        self.assertEqual(public_chain._retry_after("86400"), 300)
        self.assertEqual(public_chain._retry_after("Wed, 21 Oct 2026 07:28:00 GMT"), 60)
        with patch.object(public_chain, "pace", lambda url: None), self.assertRaises(OSError):
            self.fetch(Opener([429], {"Retry-After": "120"}))
        self.assertTrue(public_chain.rate_limited(self.URL, clock=lambda: self.clock[0] + 119))

    def test_other_errors_do_not_pause_the_host(self):
        with patch.object(public_chain, "pace", lambda url: None), self.assertRaises(OSError):
            self.fetch(Opener([503]))
        self.assertFalse(public_chain.rate_limited(self.URL, clock=lambda: self.clock[0]))

    def test_a_429_mid_read_stops_traffic_to_that_host_and_each_pair_shows_unavailable(self):
        sent = []

        class Limited:
            def open(self, request, timeout):
                sent.append(request.full_url)
                message = Message()
                message["Retry-After"] = "30"
                raise HTTPError(request.full_url, 429, "fixture", message, None)

        pairs = [("0x" + f"{index:02x}" * 20, 1) for index in range(1, 6)]
        with patch.object(public_chain, "build_opener", lambda *handlers: Limited()), \
                patch.object(public_chain, "pace", lambda url: None):
            wallets = watch._balance_check(pairs)
        self.assertEqual(len(sent), 1)
        self.assertEqual([wallet["status"] for wallet in wallets], ["unavailable"] * 5)
        self.assertNotIn("429", str(wallets))


if __name__ == "__main__":
    unittest.main()
