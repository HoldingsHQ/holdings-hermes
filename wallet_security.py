"""On-demand GoPlus reads. Results and rate limits live only in the profile gateway."""
import hashlib
import json
import re
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from threading import Lock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .ethereum import ADDR
from .public_chain import MAX_BYTES, NoRedirect, WALLET_NETWORKS, wallet_selection
from .solana_balances import is_pubkey
from .zerion import label

HOST = "api.gopluslabs.io"
APPROVAL_CHAINS = {1, 56, 8453, 42161}
MAX_TOKENS = 32
MAX_APPROVALS = 500
UNLIMITED = str(2 ** 256 - 1)
TOKEN_FLAGS = {"is_airdrop_scam": "Possible scam airdrop", "is_honeypot": "Possible honeypot"}
ADDRESS_FLAGS = {"phishing_activities": "Phishing activity", "stealing_attack": "Theft activity",
                 "honeypot_related_address": "Honeypot-related address", "blacklist_doubt": "Suspected malicious address",
                 "cybercrime": "Cybercrime flag", "financial_crime": "Financial crime flag",
                 "blackmail_activities": "Blackmail activity", "sanctioned": "Sanctions flag"}
OUTFLOW_LIMIT = 4
LOOKALIKE_LIMIT = 20
TRANSFER_STABLES = {"eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "eip155:1/erc20:0xdac17f958d2ee523a2206206994597c13d831ec7"}
CACHE_TTL = 300
_cache = OrderedDict()
_calls = deque()
_budget_lock = Lock()
MAX_CALLS = 30
WATCH_RESERVE = 6


def selection(body):
    if not isinstance(body, dict) or set(body) != {"wallets", "contracts"}:
        raise ValueError("Invalid wallet security scope")
    wallets = wallet_selection({"wallets": body["wallets"]}, solana=True)
    scopes = body["contracts"]
    if not isinstance(scopes, list) or len(scopes) != len(wallets):
        raise ValueError("Invalid token scope")
    normalized = []
    for (_, chain), contracts in zip(wallets, scopes):
        if not isinstance(contracts, list) or len(contracts) > MAX_TOKENS:
            raise ValueError("Too many token contracts")
        check = is_pubkey if chain == "solana" else lambda item: isinstance(item, str) and bool(ADDR.fullmatch(item.lower()))
        if not all(check(item) for item in contracts):
            raise ValueError("Invalid token contract")
        normalized.append(tuple(sorted({item if chain == "solana" else item.lower() for item in contracts})))
    return wallets, normalized


def get(path, params, *, caller="interactive"):
    if caller not in {"interactive", "watch"}:
        raise ValueError("Unknown GoPlus caller")
    # Reserve the last six slots for scheduled safety checks without increasing
    # the overall provider budget. Caller is selected by backend code, not input.
    now = time.monotonic()
    key = (path, tuple(params.items()))
    with _budget_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            _cache.move_to_end(key)
            return "ready", cached[1], cached[2]
        while _calls and now - _calls[0] >= 60:
            _calls.popleft()
        limit = MAX_CALLS if caller == "watch" else MAX_CALLS - WATCH_RESERVE
        if len(_calls) >= limit:
            return "rate-limited", None, None
        _calls.append(now)
    request = Request(f"https://{HOST}{path}?{urlencode(params)}", headers={
        "Accept": "application/json", "User-Agent": "Holdings-Hermes/0.1",
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return "partial", None, None
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("code") == 1:
            retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = data.get("result")
            # Keep only a small number of recent successful replies.
            with _budget_lock:
                _cache[key] = now, result, retrieved
                _cache.move_to_end(key)
                if len(_cache) > 32:
                    _cache.popitem(last=False)
            return "ready", result, retrieved
    except HTTPError as exc:
        return {401: "access-denied", 403: "access-denied", 429: "rate-limited"}.get(exc.code, "unavailable"), None, None
    except (OSError, ValueError, RecursionError):
        pass
    return "unavailable", None, None


def positive(value):
    return value == "1" or type(value) is int and value == 1


def finding(kind, wallet, chain, contract, at, title, *, spender="", **fields):
    network = "Solana" if chain == "solana" else WALLET_NETWORKS[chain][0]
    identity = hashlib.sha256(f"{kind}:{wallet}:{chain}:{contract}:{spender}".encode()).hexdigest()
    return {"id": identity, "kind": kind, "chain": network, "chainId": chain, "contract": contract,
            "at": at, "title": title, "severity": "warning", **fields}


def tokens(result, wallet, chain, contracts, at):
    if not isinstance(result, dict):
        return {"state": "unavailable", "checked": 0, "findings": []}
    checks = 0
    findings = []
    for contract in contracts:
        row = result.get(contract)
        if not isinstance(row, dict) or not row:
            continue
        checks += 1
        reasons = [title for key, title in TOKEN_FLAGS.items() if positive(row.get(key))]
        fake = row.get("fake_token")
        if isinstance(fake, dict) and positive(fake.get("value")):
            reasons.append("Possible counterfeit token")
        if chain == "solana":
            creators = row.get("creators", row.get("creator"))
            creators = [creators] if isinstance(creators, dict) else creators
            if isinstance(creators, list) and any(isinstance(item, dict) and positive(item.get("malicious_address"))
                                                 for item in creators):
                reasons.append("GoPlus flagged a token creator")
            hook = row.get("transfer_hook")
            if isinstance(hook, dict) and positive(hook.get("malicious_address")):
                reasons.append("GoPlus flagged the transfer hook")
        if reasons:
            metadata = row.get("metadata")
            symbol = metadata.get("symbol") if isinstance(metadata, dict) else row.get("token_symbol")
            findings.append(finding("token", wallet, chain, contract, at, reasons[0],
                                    asset=label(symbol, "Token"), reasons=reasons))
    return {"state": "ready" if checks == len(contracts) else "partial", "checked": checks, "findings": findings, "at": at}


def approvals(result, wallet, chain, at):
    if not isinstance(result, list):
        return {"state": "unavailable", "checked": 0, "findings": []}
    checks = 0
    seen = set()
    findings = []
    partial = len(result) > MAX_APPROVALS
    for token in result[:MAX_APPROVALS]:
        if (not isinstance(token, dict) or token.get("chain_id") not in (chain, str(chain))
                or not isinstance(token.get("token_address"), str) or not ADDR.fullmatch(token["token_address"].lower())
                or not isinstance(token.get("approved_list"), list)):
            partial = True
            continue
        contract = token["token_address"].lower()
        for row in token["approved_list"]:
            if len(seen) >= MAX_APPROVALS:
                partial = True
                break
            spender = row.get("approved_contract") if isinstance(row, dict) else None
            amount = row.get("approved_amount") if isinstance(row, dict) else None
            if not isinstance(spender, str) or not ADDR.fullmatch(spender.lower()) or not isinstance(amount, str):
                partial = True
                continue
            spender = spender.lower()
            if (contract, spender) in seen:
                partial = True
                continue
            seen.add((contract, spender))
            unlimited = amount.lower() == "unlimited" or amount == UNLIMITED
            if not unlimited and not re.fullmatch(r"[0-9]{1,78}(?:\.[0-9]{1,78})?", amount):
                partial = True
                continue
            checks += 1
            if not unlimited and not any(char in "123456789" for char in amount):
                continue
            details = row.get("address_info")
            details = details if isinstance(details, dict) else {}
            reasons = []
            if positive(token.get("malicious_address")):
                reasons.append("GoPlus flagged the token")
            if positive(details.get("doubt_list")) or (isinstance(details.get("malicious_behavior"), list)
                                                        and details["malicious_behavior"]):
                reasons.append("GoPlus flagged the spender")
            if unlimited:
                reasons.append("Unlimited token spending permission")
            if reasons:
                report = finding("approval", wallet, chain, contract, at, "Unlimited approval" if unlimited else "Flagged approval",
                                 spender=spender, asset=label(token.get("token_symbol"), "Token"), reasons=reasons,
                                 allowance="Unlimited" if unlimited else amount,
                                 reviewUrl=f"https://revoke.cash/address/{wallet}?chainId={chain}")
                findings.append({**report, "spender": spender})
    return {"state": "partial" if partial else "ready", "checked": checks, "findings": findings, "at": at}


def snapshot(wallets, contract_lists, *, caller="interactive"):
    reports = []
    for (wallet, chain), contracts in zip(wallets, contract_lists):
        token_path = "/api/v1/solana/token_security" if chain == "solana" else f"/api/v1/token_security/{chain}"
        token_report = {"state": "no-tokens", "checked": 0, "findings": []}
        if contracts:
            state, data, at = get(token_path, {"contract_addresses": ",".join(contracts)}, caller=caller)
            token_report = tokens(data, wallet, chain, contracts, at) if state == "ready" else {**token_report, "state": state}
        approval_report = {"state": "unsupported", "checked": 0, "findings": []}
        if chain in APPROVAL_CHAINS:
            state, data, at = get(f"/api/v2/token_approval_security/{chain}", {"addresses": wallet}, caller=caller)
            approval_report = approvals(data, wallet, chain, at) if state == "ready" else {**approval_report, "state": state}
        reports.append({"tokens": token_report, "approvals": approval_report})
    return {"status": "ready", "wallets": reports}


def fixture():
    wallet, token, spender = ("0x" + digit * 40 for digit in "123")
    at = "2026-11-09T12:00:00+00:00"
    return {"status": "ready", "fictional": True, "wallets": [{
        "tokens": tokens({token: {"token_symbol": "DEMO-DROP", "is_airdrop_scam": "1"}}, wallet, 1, (token,), at),
        "approvals": approvals([{"chain_id": "1", "token_address": token, "token_symbol": "DEMO-DROP",
                                 "approved_list": [{"approved_contract": spender, "approved_amount": "Unlimited"}]}], wallet, 1, at),
    }]}


def outflow_item(record, recipient, state, result, at):
    data = result if isinstance(result, dict) else {}
    reasons = [title for field, title in ADDRESS_FLAGS.items() if positive(data.get(field))]
    covered = state == "ready" and any(type(data.get(key)) in (int, str) and str(data[key]) in ("0", "1")
                                       for key in ADDRESS_FLAGS)
    return {"id": hashlib.sha256(("outflow:" + record["id"]).encode()).hexdigest(),
            "kind": "outflow", "chain": "Ethereum", "severity": "critical" if reasons else "info",
            "title": "Outgoing transfer to a flagged recipient" if reasons else "Outgoing transfer checked" if covered
            else "Outgoing transfer check unavailable", "recipient": recipient, "reasons": reasons,
            "record": record["id"], "recordAt": record["at"], "asset": record["asset"], "at": at,
            "status": "flagged" if reasons else "unflagged" if covered else state if state != "ready" else "unavailable"}


def endpoints(row):
    if len(row["observations"]) != 1:
        return None
    source = row["observations"][0]
    addresses = (source.get("from"), source.get("to"))
    if not all(isinstance(address, str) and ADDR.fullmatch(address.lower()) for address in addresses):
        return None
    sender, recipient = (address.lower() for address in addresses)
    if sender == recipient or "0x" + "0" * 40 in (sender, recipient):
        return None
    return sender, recipient


def outflow_checks(snapshot, *, caller="interactive"):
    findings = []
    seen = set()
    wallet = snapshot["wallet"]
    outgoing = sorted(snapshot["rows"], key=lambda row: row["at"], reverse=True)
    for row in outgoing:
        if row["kind"] not in {"transfer", "token_transfer"} or not row["quantity"].startswith("-"):
            continue
        pair = endpoints(row)
        if not pair or pair[0] != wallet:
            continue
        recipient = pair[1]
        if recipient in seen:
            continue
        if len(seen) == OUTFLOW_LIMIT:
            return {"findings": findings, "limited": True}
        seen.add(recipient)
        state, data, at = get(f"/api/v1/address_security/{recipient}", {"chain_id": "1"}, caller=caller)
        findings.append(outflow_item(row, recipient, state, data, at))
    return {"findings": findings, "limited": False}


def outflow_fixture():
    at = "2026-11-09T12:00:00+00:00"
    return {"findings": [outflow_item({"id": "fictional-ethereum-outflow", "at": at, "asset": "ETH"},
                                      "0x" + "4" * 40, "ready", {"phishing_activities": "1"}, at)], "limited": False}


def lookalike_checks(snapshot):
    wallet = snapshot["wallet"]
    sent = {}
    by_edges = {}
    findings = {}
    checked = 0
    for row in sorted(snapshot["rows"], key=lambda item: item["at"]):
        asset = row["asset"]
        if row["kind"] != "token_transfer" or asset not in TRANSFER_STABLES:
            continue
        pair = endpoints(row)
        if not pair:
            continue
        sender, recipient = pair
        if sender == wallet and row["quantity"].startswith("-"):
            key = (asset, recipient)
            # Keep the first evidence that established this counterparty.
            if key not in sent:
                sent[key] = row
                by_edges.setdefault((asset, recipient[2:6], recipient[-4:]), {})[recipient] = row
            continue
        if recipient != wallet or row["quantity"].startswith("-") or (asset, sender) in sent:
            continue
        checked += 1
        candidates = by_edges.get((asset, sender[2:6], sender[-4:]), {})
        previous = [{"address": address, "record": source["id"], "at": source["at"]}
                    for address, source in candidates.items() if source["at"] < row["at"]]
        if not previous:
            continue
        identity = hashlib.sha256(f"lookalike:{wallet}:{asset}:{sender}".encode()).hexdigest()
        findings[identity] = {"id": identity, "kind": "poisoning", "chain": "Ethereum", "severity": "warning",
                              "title": "Possible look-alike sender", "asset": asset, "sender": sender,
                              "record": row["id"], "at": row["at"], "known": previous[:4]}
    ordered = sorted(findings.values(), key=lambda item: item["at"], reverse=True)
    return {"findings": ordered[:LOOKALIKE_LIMIT], "checked": checked, "limited": len(ordered) > LOOKALIKE_LIMIT}


def lookalike_fixture():
    wallet = "0x" + "1" * 40
    known = "0xab12" + "2" * 32 + "cd34"
    sender = "0xab12" + "3" * 32 + "cd34"
    asset = "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    common = {"asset": asset, "kind": "token_transfer"}
    return lookalike_checks({"wallet": wallet, "rows": [
        {**common, "id": "fictional-prior-usdc-transfer", "at": "2026-11-08T12:00:00Z", "quantity": "-1",
         "observations": [{"from": wallet, "to": known}]},
        {**common, "id": "fictional-incoming-usdc-transfer", "at": "2026-11-09T12:00:00Z", "quantity": "0",
         "observations": [{"from": sender, "to": wallet}]},
    ]})
