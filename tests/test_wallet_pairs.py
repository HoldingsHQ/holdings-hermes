"""Twelve address and network pairs: request counts, provider pacing and watch reuse. Fictional data only."""
import importlib
import json
import re
import sys
import threading
import time
import unittest
from collections import Counter
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("holdings_wallet_pair_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
public_chain = importlib.import_module(f"{package.__name__}.public_chain")
solana_balances = importlib.import_module(f"{package.__name__}.solana_balances")
zerion = importlib.import_module(f"{package.__name__}.zerion")
alert_watch = importlib.import_module(f"{package.__name__}.alert_watch")
wallet_security = importlib.import_module(f"{package.__name__}.wallet_security")
performance = importlib.import_module(f"{package.__name__}.performance")

ADDRESSES = ["0x" + digit * 40 for digit in "12345"]
SOLANA = "1" * 31 + "2"
EVM = (1, 8453, 42161, 56, 4663)


def heavy_selection():
    """Twelve pairs that load the busiest hosts: five on Ethereum, five on Base, two on Arbitrum."""
    pairs = [(address, 1) for address in ADDRESSES] + [(address, 8453) for address in ADDRESSES]
    return pairs + [(ADDRESSES[0], 42161), (ADDRESSES[1], 42161)]


class Provider:
    """Answers each public read with an empty balance and records how hosts were used."""

    def __init__(self, delay=0.0):
        self.calls, self.delay = Counter(), delay
        self.active, self.overlap = Counter(), set()
        self.lock = threading.Lock()

    def __call__(self, url, payload=None, *, timeout=8):
        host = urlsplit(url).hostname
        with self.lock:
            self.calls[host] += 1
            self.active[host] += 1
            if self.active[host] > 1:
                self.overlap.add(host)
        try:
            if self.delay:
                time.sleep(self.delay)
            if host == "api.etherscan.io":
                return {"status": "1", "result": "0"}
            method, params = payload["method"], payload["params"]
            if host == "api.mainnet-beta.solana.com":
                return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 9}, "value": 0 if method == "getBalance" else []}}
            chain = next(cid for cid, (_, rpc, _) in public_chain.WALLET_NETWORKS.items() if urlsplit(rpc).hostname == host)
            result = {"eth_chainId": hex(chain), "eth_getBlockByNumber": {"number": "0x10"},
                      "eth_getBalance": "0x0", "eth_call": "0x" + "0" * 64}[method]
            return {"jsonrpc": "2.0", "id": 1, "result": result}
        finally:
            with self.lock:
                self.active[host] -= 1


class WalletPairTests(unittest.TestCase):
    def read(self, selection, key=None, delay=0.0):
        provider = Provider(delay)
        with patch.object(public_chain, "fetch", provider):
            result = public_chain.read_wallet_selection(selection, key)
        return provider, result

    def test_cap_is_twelve_and_thirteen_is_rejected(self):
        self.assertEqual(public_chain.MAX_WALLET_PAIRS, 12)
        body = [{"address": address, "chainId": chain} for address in ADDRESSES[:2] for chain in EVM]
        body += [{"address": ADDRESSES[2], "chainId": 1}, {"address": SOLANA, "chainId": "solana"}]
        self.assertEqual(len(public_chain.wallet_selection({"wallets": body}, solana=True)), 12)
        with self.assertRaises(ValueError):
            public_chain.wallet_selection({"wallets": body + [{"address": ADDRESSES[3], "chainId": 1}]}, solana=True)
        with self.assertRaises(ValueError):
            public_chain.wallet_selection({"wallets": body[:1] + [{"address": ADDRESSES[0].upper().replace("0X", "0x"), "chainId": 1}]})

    def test_keyless_read_of_twelve_pairs_makes_a_known_number_of_calls_per_host(self):
        provider, result = self.read(heavy_selection())
        self.assertEqual([(w["address"], w["chainId"]) for w in result["wallets"]], heavy_selection())
        self.assertTrue(all(w["status"] == "ready" for w in result["wallets"]))
        # Two setup calls per pair plus one call per curated asset (10 on Ethereum, 8 on Base and Arbitrum).
        self.assertEqual(dict(provider.calls), {"ethereum-rpc.publicnode.com": 60, "base-rpc.publicnode.com": 50,
                                                "arbitrum-one-rpc.publicnode.com": 20})
        self.assertEqual(provider.overlap, set())

    def test_solana_pair_makes_three_calls_and_five_stay_inside_the_public_limit(self):
        solana = ["1" * 31 + digit for digit in "23456"]
        provider, result = self.read([(address, "solana") for address in solana])
        self.assertEqual(provider.calls["api.mainnet-beta.solana.com"], 15)
        self.assertLessEqual(provider.calls["api.mainnet-beta.solana.com"], 100)
        self.assertEqual(len(result["wallets"]), 5)

    def test_keyed_read_sends_ethereum_and_arbitrum_to_etherscan_in_one_queue(self):
        selection = [(a, 1) for a in ADDRESSES] + [(a, 42161) for a in ADDRESSES] + [(ADDRESSES[0], 56), (ADDRESSES[1], 56)]
        with patch.object(public_chain, "ETHERSCAN_SPACING", 0):
            provider, result = self.read(selection, key="fictional-key", delay=0.002)
        self.assertEqual(provider.calls["api.etherscan.io"], 5 * 10 + 5 * 8)
        self.assertEqual(provider.calls["bsc-rpc.publicnode.com"], 20)
        self.assertEqual(provider.overlap, set())
        self.assertEqual([w["source"] for w in result["wallets"]][:10], ["etherscan"] * 10)

    def test_groups_run_side_by_side_but_never_overlap_on_one_host(self):
        mixed = heavy_selection()[:6] + [(ADDRESSES[0], 56), (ADDRESSES[0], 4663), (SOLANA, "solana")]
        provider, result = self.read(mixed, delay=0.001)
        self.assertEqual(provider.overlap, set())
        self.assertEqual([(w["address"], w["chainId"]) for w in result["wallets"]], mixed)

    def test_pace_spaces_one_host_and_leaves_other_hosts_alone(self):
        now, slept = [100.0], []
        clock = lambda: now[0]

        def sleep(seconds):
            slept.append(round(seconds, 3))
            now[0] += seconds

        with patch.dict(public_chain._next_slot, clear=True):
            for _ in range(3):
                public_chain.pace("https://api.etherscan.io/v2/api?module=account", clock=clock, sleep=sleep)
            public_chain.pace("https://base-rpc.publicnode.com", clock=clock, sleep=sleep)
            public_chain.pace("https://base-rpc.publicnode.com", clock=clock, sleep=sleep)
            public_chain.pace("https://api.mainnet-beta.solana.com", clock=clock, sleep=sleep)
            public_chain.pace("https://api.mainnet-beta.solana.com", clock=clock, sleep=sleep)
        self.assertEqual(slept, [0.35, 0.35, 0.1, 0.25])
        self.assertEqual(public_chain.PROVIDER_SPACING["api.zerion.io"], 0.35)

    def test_zerion_requests_go_through_the_pacer(self):
        source = (ROOT / "zerion.py").read_text()
        self.assertIn("pace(request.full_url)", source)
        self.assertLess(source.index("pace(request.full_url)"), source.index("build_opener(NoRedirect()).open(request, timeout=5)"))

    def test_goplus_budget_fits_twelve_pairs_in_one_interactive_pass(self):
        calls_per_pass = 2 * public_chain.MAX_WALLET_PAIRS
        self.assertEqual(wallet_security.MAX_CALLS, 30)
        self.assertLessEqual(calls_per_pass, wallet_security.MAX_CALLS - wallet_security.WATCH_RESERVE)
        self.assertGreater(2 * (public_chain.MAX_WALLET_PAIRS + 1), wallet_security.MAX_CALLS - wallet_security.WATCH_RESERVE)

    def test_largest_twelve_pair_bodies_fit_the_route_limits(self):
        api = (ROOT / "dashboard" / "plugin_api.py").read_text()

        def limit(route):
            block = api[api.index(f'@router.post("{route}")'):]
            return int(re.search(r"len\(payload\) \+ len\(chunk\) > (\d+)", block).group(1))

        pairs = [{"address": "1" * 44, "chainId": "solana"}] * 5 + [{"address": "0x" + "a" * 40, "chainId": 42161}] * 7
        size = lambda body: len(json.dumps(body, separators=(",", ":")).encode())
        self.assertLess(size({"wallets": pairs}), limit("/wallets/balances"))
        self.assertLess(size({"wallets": pairs[5:], "section": "chart-month"}), limit("/wallets/zerion"))
        contracts = [["1" * 44] * 32] * 5 + [["0x" + "b" * 40] * 32] * 7
        self.assertLess(size({"wallets": pairs, "contracts": contracts}), limit("/wallets/security"))
        self.assertLess(size({"action": "wallets", "wallets": pairs}), limit("/alerts/watch"))


class PerformanceScopeTests(unittest.TestCase):
    def test_a_full_twelve_pair_read_keeps_every_history_stream(self):
        saved = {}

        def open_store(mode):
            return saved.get("value"), lambda value: saved.__setitem__("value", value)

        def entry(at):
            return {"at": at, "observed": True, "complete": True, "valueUsd": "1", "lines": []}

        scopes = [performance.scope_id([[address, chain]]) for address, chain in heavy_selection()]
        scopes.append(performance.scope_id(sorted(heavy_selection())))
        for at in ("2026-11-11T01:00:00+00:00", "2026-11-11T01:05:00+00:00"):
            views = performance.record({key: entry(at) for key in scopes}, open_store=open_store)
        self.assertEqual(len(saved["value"]["streams"]), 13)
        self.assertTrue(all(len(points) == 2 for points in saved["value"]["streams"].values()))
        self.assertTrue(all(len(view["points"]) == 2 for view in views.values()))
        self.assertGreaterEqual(performance.MAX_SCOPES, 2 * (public_chain.MAX_WALLET_PAIRS + 1))


class WatchReuseTests(unittest.TestCase):
    def setUp(self):
        alert_watch._hacks_cache.clear()
        self.addCleanup(alert_watch._hacks_cache.clear)

    def test_complete_defi_scan_is_reused_for_an_hour_and_rescanned_on_scope_change(self):
        selection = heavy_selection()
        complete = {"wallets": [{"state": "complete", "rows": []} for _ in selection]}
        now = [1000.0]
        with patch.object(zerion, "snapshot", return_value=complete) as snapshot:
            for _ in range(3):
                alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 1)
            now[0] += alert_watch.HACKS_REFRESH - 1
            alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 1)
            now[0] += 2
            alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 2)
            alert_watch._defi_positions(selection[:3], "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 3)

    def test_incomplete_scan_retries_after_fifteen_minutes(self):
        selection = heavy_selection()[:2]
        limited = {"wallets": [{"state": "rate-limited", "rows": []}]}
        now = [50.0]
        with patch.object(zerion, "snapshot", return_value=limited) as snapshot:
            alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            now[0] += alert_watch.HACKS_RETRY - 1
            alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 1)
            now[0] += 2
            alert_watch._defi_positions(selection, "fictional-key", clock=lambda: now[0])
            self.assertEqual(snapshot.call_count, 2)

    def test_watch_day_of_defi_scans_stays_under_the_zerion_sublimit(self):
        scans_per_day = 24 * 3600 // alert_watch.HACKS_REFRESH
        pages = scans_per_day * public_chain.MAX_WALLET_PAIRS
        self.assertLessEqual(pages, 500)


if __name__ == "__main__":
    unittest.main()
