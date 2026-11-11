"""HOLD-05: interactive GoPlus traffic cannot consume the watcher's reserve."""
import json
from unittest.mock import patch
from tests.security import harness

ws = harness.mod("wallet_security")
watch = harness.mod("alert_watch")
TOKEN = "0x" + "ee" * 20


def address(index):
    return "0x" + format(index, "040x")


class WatcherBudgetTests(harness.SecurityTest):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.now = 1000.0
        for target, name, value in ((ws, "_calls", ws.deque()), (ws, "_cache", ws.OrderedDict()),
                                    (ws.time, "monotonic", lambda: self.now),
                                    (ws, "build_opener", harness.fake_opener(self.respond))):
            stub = patch.object(target, name, value)
            stub.start()
            self.addCleanup(stub.stop)

    def respond(self, url, data):
        self.requests.append((url, data))
        if "/token_security/" in url:
            result = {TOKEN: {"is_airdrop_scam": "1", "token_symbol": "Fictional"}}
        elif "/token_approval_security/" in url:
            result = []
        else:
            result = {"phishing_activities": "1"}
        return json.dumps({"code": 1, "result": result}).encode()

    def saturate_interactive(self):
        return [ws.get("/api/v1/address_security/" + address(i), {"chain_id": "1"})[0]
                for i in range(30)]

    def security(self, index, *, token=False):
        return watch._security([(address(index), 1)], [{"status": "ready", "balances":
            [{"contract": TOKEN, "units": "1"}] if token else []}])

    def test_watcher_reserve_global_cap_cache_and_window_recovery(self):
        states = self.saturate_interactive()
        state, findings = self.security(100, token=True)
        self.assertEqual(state, "ready", "interactive reads starved the actual alert watcher")
        self.assertEqual(len(findings), 1)
        self.assertEqual(states.count("ready"), 24)
        self.assertEqual(states.count("rate-limited"), 6)
        count = len(self.requests)
        self.assertEqual(self.security(100, token=True), (state, findings))
        self.assertEqual(len(self.requests), count, "fresh cached replies must not spend budget")
        for i in range(101, 105):
            self.assertEqual(self.security(i)[0], "ready")
        self.assertEqual(len(self.requests), 30)
        self.assertEqual(self.security(105), ("partial", []))
        self.assertEqual(len(self.requests), 30, "the overall 30/minute cap must remain intact")
        self.now += 60
        self.assertEqual(self.security(105)[0], "ready")
        self.assertEqual(len(self.requests), 31)

    def history(self, index):
        wallet, recipient = address(1000), address(index)
        row = {"id": "etherscan:txlist:fictional:1", "kind": "transfer", "quantity": "-1",
               "asset": "eip155:1/slip44:60", "at": "2026-11-09T00:00:00Z",
               "observations": [{"from": wallet, "to": recipient}]}
        with patch.object(watch.ethereum_review, "review", return_value={"wallet": wallet, "rows": [row],
                                                                           "coverage": [{"complete": True}]}):
            return watch._history([(wallet, 1)], [{"status": "ready", "block": 1000}],
                                  "synthetic-key", {"outflow"})["outflow"]

    def test_outflow_watcher_uses_reserve_and_reports_exhaustion_as_partial(self):
        self.saturate_interactive()
        state, findings = self.history(100)
        self.assertEqual(state, "ready")
        self.assertEqual(len(findings), 1, "interactive traffic suppressed a flagged-outflow finding")
        self.assertEqual(findings[0]["severity"], "critical")
        for i in range(101, 106):
            self.assertEqual(len(self.history(i)[1]), 1)
        self.assertEqual(self.history(106), ("partial", []))
        self.assertEqual(len(self.requests), 30)
        saved = watch.default_state(watch.MODE)
        saved["checks"] = {"outflow": self.history(106)[0]}
        self.assertEqual(watch._view(saved)["checks"]["outflow"], "partial")

    def test_upstream_rate_limit_is_not_hidden_by_reserved_budget(self):
        from urllib.error import HTTPError
        self.saturate_interactive()
        attempts = []
        def limited(url, data):
            attempts.append(url)
            raise HTTPError(url, 429, "synthetic limit", {}, None)
        with patch.object(ws, "build_opener", harness.fake_opener(limited)):
            self.assertEqual(self.security(100), ("partial", []))
        self.assertEqual(len(attempts), 1, "the watcher's reserved request never reached the fake opener")
        saved = watch.default_state(watch.MODE)
        saved["checks"] = {"security": "partial"}
        self.assertEqual(watch._view(saved)["checks"]["security"], "partial")
