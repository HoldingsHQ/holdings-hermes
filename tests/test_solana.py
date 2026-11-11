"""Fictional Solana payloads. No network or credentials."""
import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType

package = ModuleType("holdings_solana_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
solana = importlib.import_module(f"{package.__name__}.solana_balances")
public_chain = importlib.import_module(f"{package.__name__}.public_chain")

OWNER = "1" * 31 + "2"
MINT = "1" * 31 + "3"
ACCOUNT = "1" * 31 + "4"
OTHER = "1" * 31 + "5"


def result(value, slot=33):
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": slot}, "value": value}}


def token(key, program, amount, *, mint=MINT, owner=OWNER, decimals=18):
    return {"pubkey": key, "account": {"owner": program, "data": {"parsed": {"type": "account", "info": {
        "owner": owner, "mint": mint, "tokenAmount": {"amount": amount, "decimals": decimals, "uiAmount": 0},
    }}}}}


class SolanaTests(unittest.TestCase):
    def test_scope_validates_32_byte_base58_and_excludes_zerion(self):
        evm = "0x" + "AB" * 20
        payload = {"wallets": [{"address": OWNER, "chainId": "solana"}, {"address": evm, "chainId": 1}]}
        self.assertEqual(public_chain.wallet_selection(payload, solana=True), [(OWNER, "solana"), (evm.lower(), 1)])
        with self.assertRaises(ValueError):
            public_chain.wallet_selection(payload)
        for address in (OWNER[:-1], "1" + OWNER, "0" + OWNER[1:], "z" * 44):
            self.assertFalse(solana.is_pubkey(address))

    def test_one_address_expands_to_network_pairs_with_a_cap_and_per_pair_duplicates(self):
        evm, other = "0x" + "cd" * 20, "0x" + "ef" * 20
        chains = (1, 8453, 42161, 56, 4663)
        pairs = [{"address": address, "chainId": chain} for address in (evm, other) for chain in chains]
        full = pairs + [{"address": OWNER, "chainId": "solana"}, {"address": "0x" + "ab" * 20, "chainId": 1}]
        selection = public_chain.wallet_selection({"wallets": full}, solana=True)
        self.assertEqual(public_chain.MAX_WALLET_PAIRS, 12)
        self.assertEqual(len(selection), public_chain.MAX_WALLET_PAIRS)
        self.assertEqual({chain for _, chain in selection}, {1, 8453, 42161, 56, 4663, "solana"})
        with self.assertRaises(ValueError):
            public_chain.wallet_selection({"wallets": full + [{"address": "0x" + "12" * 20, "chainId": 1}]}, solana=True)
        with self.assertRaises(ValueError):
            public_chain.wallet_selection({"wallets": [pairs[0], {"address": evm.upper().replace("0X", "0x"), "chainId": 1}]})
        same_address_other_network = public_chain.wallet_selection({"wallets": [pairs[0], pairs[1]]})
        self.assertEqual(same_address_other_network, [(evm, 1), (evm, 8453)])

    def test_exact_balances_merge_mint_accounts_and_keep_program_evidence(self):
        calls = []
        spl, token_2022 = (program for _, program in solana.PROGRAMS)

        def fetch(url, payload, *, timeout):
            self.assertEqual(url, solana.RPC)
            self.assertEqual(timeout, 7)
            method, params = payload["method"], payload["params"]
            self.assertEqual(params[0], OWNER)
            calls.append(method)
            if method == "getBalance":
                self.assertEqual(params[1], {"commitment": "finalized"})
                return result(1000000001, 30)
            self.assertEqual(params[2], {"commitment": "finalized", "minContextSlot": 30, "encoding": "jsonParsed"})
            program = params[1]["programId"]
            if program == spl:
                first = token(ACCOUNT, spl, "18446744073709551614")
                return result([first, first, token(OTHER, spl, "2")], 34)
            self.assertEqual(program, token_2022)
            return result([token(ACCOUNT, token_2022, "1", mint=OTHER, decimals=0)], 35)

        snapshot = solana.snapshot(OWNER, fetch)
        self.assertEqual(calls, ["getBalance", "getTokenAccountsByOwner", "getTokenAccountsByOwner"])
        self.assertEqual(snapshot["coverage"], {"SOL": "ready", "SPL Token": "ready", "Token-2022": "ready"})
        sol, classic, extended = snapshot["balances"]
        self.assertEqual((sol["units"], sol["slot"]), ("1.000000001", "30"))
        self.assertEqual((classic["units"], classic["contract"], classic["slot"], classic["accounts"]),
                         ("18.446744073709551616", MINT, "34", [ACCOUNT, OTHER]))
        self.assertEqual((extended["units"], extended["program"]), ("1", "Token-2022"))

    def test_partial_unavailable_does_not_look_empty_or_zero(self):
        def fetch(url, payload, *, timeout):
            if payload["method"] == "getBalance":
                raise OSError("fictional timeout")
            program = payload["params"][1]["programId"]
            if program == solana.PROGRAMS[0][1]:
                return result([token(ACCOUNT, program, "1", owner=OTHER)])
            return result([])
        snapshot = solana.snapshot(OWNER, fetch)
        self.assertEqual(snapshot["status"], "ready")
        self.assertEqual(snapshot["balances"], [])
        self.assertEqual(snapshot["coverage"], {"SOL": "unavailable", "SPL Token": "unavailable", "Token-2022": "ready"})

    def test_conflicting_accounts_and_bounded_inventory(self):
        label, program = solana.PROGRAMS[0]
        row = token(ACCOUNT, program, "1")
        for rows in ([row, token(ACCOUNT, program, "2")], [row] * (solana.MAX_ACCOUNTS + 1)):
            with self.assertRaises(ValueError):
                solana.token_balances(rows, OWNER, program, label, 35)
