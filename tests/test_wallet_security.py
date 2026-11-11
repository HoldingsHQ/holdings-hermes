"""GoPlus wallet checks use synthetic records and no network."""
import io
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

package = ModuleType("holdings_security_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
from holdings_security_tests import wallet_security as security

WALLET = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20
SPENDER = "0x" + "33" * 20
MINT = "1" * 31 + "2"


class WalletSecurityTests(unittest.TestCase):
    def test_scope_preserves_solana_case_and_rejects_untrusted_ids(self):
        wallets = [{"address": WALLET, "chainId": 4663}, {"address": MINT, "chainId": "solana"}]
        payload = {"wallets": wallets, "contracts": [[TOKEN, TOKEN], [MINT]]}
        self.assertEqual(security.selection(payload), ([(WALLET, 4663), (MINT, "solana")], [(TOKEN,), (MINT,)]))
        for contracts in ([[TOKEN] * 33, [MINT]], [["https://evil.invalid"], [MINT]], [[TOKEN], [TOKEN]]):
            with self.assertRaises(ValueError):
                security.selection({**payload, "contracts": contracts})

    def test_only_requested_tokens_are_used_and_missing_data_is_partial(self):
        result = {TOKEN: {"is_airdrop_scam": "1", "token_symbol": "Sample\n\u202eToken"},
                  SPENDER: {"is_honeypot": "1"}}
        report = security.tokens(result, WALLET, 1, (TOKEN, WALLET), "date")
        self.assertEqual(report["state"], "partial")
        self.assertEqual(report["checked"], 1)
        [item] = report["findings"]
        self.assertEqual((item["title"], item["asset"], item["contract"]), ("Possible scam airdrop", "SampleToken", TOKEN))

    def test_approval_identity_exact_amount_and_link(self):
        approval = {"approved_contract": SPENDER, "approved_amount": "0.00000000000000000001",
                    "address_info": {"doubt_list": "1"}}
        token = {"chain_id": "8453", "token_address": TOKEN, "token_symbol": "DEMO", "approved_list": [approval]}
        [item] = security.approvals([token], WALLET, 8453, "date")["findings"]
        self.assertEqual(item["allowance"], approval["approved_amount"])
        self.assertEqual((item["contract"], item["spender"]), (TOKEN, SPENDER))
        self.assertEqual(item["reviewUrl"], f"https://revoke.cash/address/{WALLET}?chainId=8453")
        self.assertEqual(security.approvals([token], WALLET, 1, "date")["state"], "partial")
        approval["approved_amount"] = "0.00"
        self.assertEqual(security.approvals([token], WALLET, 8453, "date")["findings"], [])

    def test_solana_and_robinhood_only_call_token_endpoints(self):
        with patch.object(security, "get", return_value=("ready", {}, "date")) as get:
            result = security.snapshot([(WALLET, 4663), (MINT, "solana")], [(TOKEN,), (MINT,)])
        self.assertEqual([wallet["approvals"]["state"] for wallet in result["wallets"]], ["unsupported"] * 2)
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         ["/api/v1/token_security/4663", "/api/v1/solana/token_security"])
        report = security.tokens({MINT: {"creators": [{"malicious_address": "1"}]}}, MINT, "solana", (MINT,), "date")
        self.assertEqual(report["findings"][0]["title"], "GoPlus flagged a token creator")

    def test_request_cache_quota_and_fixture_isolation(self):
        path, params = "/api/v1/token_security/1", {"contract_addresses": TOKEN}
        with patch.object(security, "_cache", security.OrderedDict()), patch.object(security, "_calls", security.deque()), \
                patch.object(security, "build_opener") as build:
            open_request = build.return_value.open
            open_request.return_value = io.BytesIO(b'{"code":1,"result":{}}')
            self.assertEqual(security.get(path, params)[0], "ready")
            self.assertEqual(security.get(path, params)[0], "ready")
            self.assertEqual(open_request.call_count, 1)
            request = open_request.call_args.args[0]
            url = urlsplit(request.full_url)
            self.assertEqual((url.scheme, url.hostname, request.get_method()), ("https", security.HOST, "GET"))
            self.assertEqual(parse_qs(url.query), {"contract_addresses": [TOKEN]})
            self.assertIsNone(request.get_header("Authorization"))
            security._calls.extend([security.time.monotonic()] * 29)
            self.assertEqual(security.get(path, {"contract_addresses": SPENDER})[0], "rate-limited")
            self.assertTrue(security.fixture()["fictional"])
            self.assertEqual(open_request.call_count, 1)

    def test_outflow_checks_use_only_unique_verified_outgoing_recipients(self):
        def transfer(index, recipient, *, sender=WALLET, quantity="-1", kind="transfer"):
            return {"id": f"row-{index}", "at": f"2026-11-09T12:00:{index:02d}Z", "asset": "ETH",
                    "kind": kind, "quantity": quantity, "observations": [{"from": sender, "to": recipient}]}

        addresses = ["0x" + str(digit) * 40 for digit in range(2, 8)]
        rows = [transfer(index, recipient) for index, recipient in enumerate(addresses)]
        rows.extend((transfer(10, addresses[-1]), transfer(11, SPENDER, kind="defi_swap"),
                     transfer(12, SPENDER, sender=TOKEN), transfer(13, SPENDER, quantity="1"),
                     transfer(14, "0x" + "0" * 40)))
        with patch.object(security, "get", return_value=("ready", {"phishing_activities": "1"}, "date")) as get:
            report = security.outflow_checks({"wallet": WALLET, "rows": rows})
        self.assertTrue(report["limited"])
        self.assertEqual([item["recipient"] for item in report["findings"]], list(reversed(addresses[-4:])))
        self.assertTrue(all(item["severity"] == "critical" for item in report["findings"]))
        self.assertEqual(get.call_count, 4)
        self.assertTrue(all(call.args[1] == {"chain_id": "1"} for call in get.call_args_list))

    def test_missing_risk_data_does_not_clear_a_recipient(self):
        row = {"id": "fictional-transfer", "at": "date", "asset": "ETH"}
        self.assertEqual(security.outflow_item(row, SPENDER, "ready", {}, "date")["status"], "unavailable")
        self.assertEqual(security.outflow_item(row, SPENDER, "ready", {"stealing_attack": "0"}, "date")["status"], "unflagged")
        self.assertEqual(security.outflow_fixture()["findings"][0]["severity"], "critical")

    def test_lookalike_requires_earlier_outbound_same_token_and_both_edges(self):
        asset = "eip155:1/erc20:0xdac17f958d2ee523a2206206994597c13d831ec7"
        known = "0xabcd" + "2" * 32 + "9876"
        lookalike = "0xabcd" + "3" * 32 + "9876"

        def transfer(key, day, sender, recipient, quantity, token=asset):
            return {"id": key, "at": f"2026-11-{day:02d}T12:00:00Z", "asset": token, "kind": "token_transfer",
                    "quantity": quantity, "observations": [{"from": sender, "to": recipient}]}

        rows = [transfer("incoming", 5, lookalike, WALLET, "0"), transfer("prior", 2, WALLET, known, "-1"),
                transfer("known", 6, known, WALLET, "1"), transfer("too-early", 1, lookalike, WALLET, "0"),
                transfer("one-edge", 4, lookalike[:-1] + "5", WALLET, "1"),
                transfer("other-token", 5, lookalike, WALLET, "1", "unsupported"),
                transfer("known-earlier", 6, WALLET, lookalike, "-1"), transfer("now-known", 7, lookalike, WALLET, "1")]
        [finding] = security.lookalike_checks({"wallet": WALLET, "rows": rows})["findings"]
        self.assertEqual(finding["record"], "incoming")
        self.assertEqual(finding["known"][0]["record"], "prior")
        self.assertEqual(finding["severity"], "warning")
        self.assertEqual(len(security.lookalike_fixture()["findings"]), 1)
