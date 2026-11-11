"""Fictional Ethereum review checks with injected upstream evidence."""
import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType

from tests.fixtures import HASH, POOL, WALLET

package = ModuleType("holdings_ethereum_fixture")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
chain = importlib.import_module(f"{package.__name__}.ethereum")
adapter = importlib.import_module(f"{package.__name__}.ethereum_review")


class EthereumReviewTests(unittest.TestCase):
    def test_verified_swap_replaces_token_observations_and_keeps_exact_fee(self):
        tokens = sorted(chain.TOKENS)
        tx = {"hash": HASH, "blockNumber": "10", "timeStamp": "1767225600",
              "from": WALLET, "to": sorted(chain.ROUTERS)[0], "gasUsed": "21000",
              "gasPrice": "1000000001", "input": "0x01", "isError": "0"}
        events = [
            {**tx, "contractAddress": tokens[0], "from": WALLET, "to": POOL},
            {**tx, "contractAddress": tokens[1], "from": POOL, "to": WALLET},
        ]
        def history(wallet, start, end, key, page_size, max_pages, *, timeout):
            self.assertEqual((wallet, start, end, page_size, max_pages), (WALLET, 9, 11, 25, 3))
            return {"txlist": [tx], "tokentx": events}, {"streams": {
                "txlist": {"pages": 1, "failed_pages": [], "end_of_results": True},
                "tokentx": {"pages": 1, "failed_pages": [2], "end_of_results": False},
            }}
        def verify(wallet, txid):
            return {"status": "verified", "block": 10, "transaction": txid, "wallet": wallet,
                    "rows": [{"id": "leg-" + token, "kind": "defi_swap", "asset": token,
                              "quantity": amount, "at": "2026-01-01T00:00:00+00:00",
                              "evidence": ["receipt"]} for token, amount in zip(tokens, ("-2", "0.001"))]}
        result = adapter.review(WALLET, "9", "11", "fictional", history=history, verify=verify)
        self.assertEqual([row["kind"] for row in result["rows"]], ["network_fee", "defi_swap", "defi_swap"])
        self.assertEqual(result["fee_totals"][adapter.ETH], "0.000021000000021000")
        self.assertTrue(all("receipt" in row["evidence"] and len(row["observations"]) == 3
                            for row in result["rows"][1:]))
        self.assertFalse(result["coverage"][1]["complete"])

    def test_indistinguishable_token_rows_stay_unresolved(self):
        event = {"hash": HASH, "contractAddress": POOL, "timeStamp": "1767225600",
                 "tokenDecimal": "18", "value": "1", "from": WALLET, "to": POOL}
        def history(*args, **kwargs):
            return {"txlist": [], "tokentx": [event, event.copy()]}, {"streams": {}}
        result = adapter.review(WALLET, 9, 11, "fictional", history=history)
        self.assertEqual(result["rows"], [])
        self.assertEqual(len(result["unresolved"]), 2)
        self.assertEqual(list(result["unresolved_observations"].values()), [event, event])
