"""Keyless read-only Solana snapshots. No token metadata, NFT or protocol valuation."""
import re
from datetime import datetime, timezone

from .ledger import as_units

RPC = "https://api.mainnet-beta.solana.com"
PROGRAMS = (
    ("SPL Token", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
    ("Token-2022", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
)
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
PUBKEY = re.compile("[" + BASE58 + "]{32,44}")
MAX_ACCOUNTS = 200


def is_pubkey(value):
    if not isinstance(value, str) or not PUBKEY.fullmatch(value):
        return False
    integer = 0
    for char in value:
        integer = integer * 58 + BASE58.index(char)
    leading = len(value) - len(value.lstrip("1"))
    return leading + (integer.bit_length() + 7) // 8 == 32


def read(fetch, method, params):
    response = fetch(RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=7)
    if not isinstance(response, dict) or response.get("id") != 1 or response.get("jsonrpc") != "2.0":
        raise ValueError("Solana response unavailable")
    result = response["result"]
    slot = result["context"]["slot"]
    if type(slot) is not int or not 0 <= slot < 2 ** 64:
        raise ValueError("Invalid finalized slot")
    return slot, result["value"]


def token_balances(values, wallet, program, label, slot):
    if not isinstance(values, list) or len(values) > MAX_ACCOUNTS:
        raise ValueError("Token inventory exceeds the local bounds")
    seen, totals = {}, {}
    for row in values:
        account = row["account"]
        parsed = account["data"]["parsed"]
        info = parsed["info"]
        key, mint = row["pubkey"], info["mint"]
        amount, decimals = info["tokenAmount"]["amount"], info["tokenAmount"]["decimals"]
        if (account["owner"] != program or parsed["type"] != "account" or info["owner"] != wallet
                or not is_pubkey(key) or not is_pubkey(mint) or not isinstance(amount, str)
                or not re.fullmatch(r"[0-9]{1,20}", amount) or int(amount) >= 2 ** 64
                or type(decimals) is not int or not 0 <= decimals <= 255):
            raise ValueError("Invalid token account evidence")
        atomic = int(amount)
        details = (mint, atomic, decimals)
        if key in seen:
            if seen[key] != details:
                raise ValueError("Conflicting token account")
            continue
        seen[key] = details
        total, places, accounts = totals.get(mint, (0, decimals, []))
        if places != decimals:
            raise ValueError("Conflicting mint decimals")
        totals[mint] = (total + atomic, places, accounts + [key])

    return [{"asset": f"{label} · {mint[:5]}…{mint[-4:]}", "contract": mint, "decimals": places,
             "units": format(as_units(total, places), "f"), "slot": str(slot), "program": label, "accounts": accounts}
            for mint, (total, places, accounts) in sorted(totals.items()) if total]


def snapshot(address, fetch):
    if not is_pubkey(address):
        raise ValueError("Invalid Solana public address")
    options = {"commitment": "finalized"}
    balances, coverage = [], {}
    try:
        slot, lamports = read(fetch, "getBalance", [address, options])
        if type(lamports) is not int or not 0 <= lamports < 2 ** 64:
            raise ValueError("Invalid native balance")
        balances.append({"asset": "SOL", "contract": None, "decimals": 9,
                         "units": format(as_units(lamports, 9), "f"), "slot": str(slot)})
        coverage["SOL"] = "ready"
        options["minContextSlot"] = slot
    except (KeyError, OSError, TypeError, ValueError):
        coverage["SOL"] = "unavailable"
    for label, program in PROGRAMS:
        try:
            slot, result = read(fetch, "getTokenAccountsByOwner", [
                address, {"programId": program}, {**options, "encoding": "jsonParsed"},
            ])
            if slot < options.get("minContextSlot", 0):
                raise ValueError("Stale token account response")
            balances.extend(token_balances(result, address, program, label, slot))
            coverage[label] = "ready"
        except (KeyError, OSError, TypeError, ValueError):
            coverage[label] = "unavailable"
    return {"status": "ready" if "ready" in coverage.values() else "unavailable", "source": "solana",
            "balances": balances, "coverage": coverage,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
