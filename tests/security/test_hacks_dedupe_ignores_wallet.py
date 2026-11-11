"""HOLD-03: incident and outflow alerts retain wallet/chain/position identity."""
from unittest.mock import patch
from tests.security import harness

watch = harness.mod("alert_watch")
W1, W2 = ("0x" + "ab" * 20, 1), ("0x" + "cd" * 20, 1)
INCIDENT = {"id": "i" * 64, "name": "Aave V3", "chainIds": [1, 8453],
            "at": "2026-11-09T00:00:00+00:00", "networks": ["Ethereum", "Base"], "technique": ""}


class FindingIdentityTests(harness.SecurityTest):
    def hacks(self, pairs, position="same-position"):
        watch._hacks_cache.clear()  # each case reads its own fixture, so skip the hourly watch cache
        with patch.object(watch.zerion, "snapshot", return_value={"wallets": [
                {"state": "complete", "rows": [{"protocol": "aave-v3", "id": position}]} for _ in pairs]}), \
                patch.object(watch.protocol_incidents, "snapshot", return_value={"status": "ready", "incidents": [INCIDENT]}):
            return watch._hacks(pairs, "synthetic-key")[1]

    def test_same_incident_on_two_wallets_is_delivered_twice(self):
        a, b = self.hacks([W1]), self.hacks([W2])
        self.assertNotEqual(a[0]["id"], b[0]["id"])
        state = watch.default_state(watch.MODE)
        self.assertEqual(len(watch.ingest(state, a)), 1)
        self.assertEqual(len(watch.ingest(state, b)), 1)
        self.assertEqual(watch.ingest(state, a + b), [])
        self.assertEqual(a[0]["id"], self.hacks([(W1[0].upper(), 1)])[0]["id"])

    def test_same_wallet_chain_and_position_are_distinct(self):
        a = self.hacks([W1])[0]["id"]
        self.assertNotEqual(a, self.hacks([(W1[0], 8453)])[0]["id"])
        self.assertNotEqual(a, self.hacks([W1], "other-position")[0]["id"])

    def outflow(self, pair, asset):
        item = {"id": "synthetic-id", "severity": "critical", "record": "etherscan:tokentx:synthetic-tx:1",
                "recipient": "0x" + "ee" * 20, "title": "Flagged recipient", "at": INCIDENT["at"], "asset": asset}
        with patch.object(watch.ethereum_review, "review", return_value={"coverage": [{"complete": True}]}), \
                patch.object(watch.wallet_security, "outflow_checks", return_value={"findings": [item]}):
            return watch._history([pair], [{"status": "ready", "block": 1000}], "synthetic-key", {"outflow"})["outflow"][1][0]

    def test_outflows_in_same_transaction_preserve_wallet_and_asset(self):
        a = self.outflow(W1, "eip155:1/erc20:" + "0x" + "12" * 20)
        b = self.outflow(W2, "eip155:1/erc20:" + "0x" + "12" * 20)
        c = self.outflow(W1, "eip155:1/erc20:" + "0x" + "34" * 20)
        self.assertEqual(len({a["id"], b["id"], c["id"]}), 3)
        state = watch.default_state(watch.MODE)
        self.assertEqual(len(watch.ingest(state, [a, b, c])), 3)
        self.assertEqual(watch.ingest(state, [a, b, c]), [])
