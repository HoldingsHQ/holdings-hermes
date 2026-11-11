"""Conservative transfer suggestions and encrypted, profile-scoped decisions."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from .ledger import number
from .decision_history import append_event, trail
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

MODE = "transfer-decisions"
KINDS = {"withdrawal", "deposit", "transfer", "token_transfer"}
SOURCES = {"kraken-spot", "binance-spot", "bybit-spot", "bybit-csv", "ethereum"}
ASSET = re.compile(r"eip155:[1-9][0-9]{0,15}/(?:slip44:[0-9]{1,10}|erc20:0x[a-f0-9]{40})")
TX = re.compile(r"0x[a-f0-9]{64}")
REF = re.compile(r"[A-Za-z0-9:_/.-]{1,240}")
MAX_ROWS = 300
MAX_SUGGESTIONS = 60
MAX_DECISIONS = 160


def _valid(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _fact(row):
    if not isinstance(row, dict) or row.get("kind") not in KINDS:
        return None
    meta = row.get("transfer")
    if not isinstance(meta, dict) or not _valid(ASSET, meta.get("asset")):
        return None
    if (row.get("sourceMode") not in SOURCES
            or not all(_valid(REF, meta.get(field)) for field in ("ref", "account"))
            or not _valid(REF, row.get("id")) or not _valid(REF, row.get("originalId"))):
        raise ValueError("Invalid transfer reference")
    tx = meta.get("hash", "")
    if tx != "" and not _valid(TX, tx):
        raise ValueError("Invalid transaction hash")
    if not isinstance(row.get("at"), str):
        raise ValueError("A transfer timestamp needs an offset")
    at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("A transfer timestamp needs an offset")
    amount, fee = number(row["quantity"]), number(row["fee"])
    if not amount or fee < 0:
        return None
    if (row["kind"] == "withdrawal" and amount > 0) or (row["kind"] == "deposit" and amount < 0):
        return None
    return {"ref": f"{row['sourceMode']}:{meta['ref']}", "account": meta["account"],
            "asset": meta["asset"], "tx": tx, "amount": amount, "fee": fee,
            "fee_asset": meta.get("feeAsset") if _valid(ASSET, meta.get("feeAsset")) else "",
            "at": at.astimezone(timezone.utc), "row": row}


def _pair(outgoing, incoming):
    if (outgoing["asset"] != incoming["asset"] or outgoing["account"] == incoming["account"]
            or outgoing["ref"] == incoming["ref"] or outgoing["amount"] >= 0 or incoming["amount"] <= 0):
        return None
    hashes = outgoing["tx"], incoming["tx"]
    if all(hashes) and hashes[0] != hashes[1]:
        return None
    same_hash = bool(hashes[0] and hashes[0] == hashes[1])
    elapsed = (incoming["at"] - outgoing["at"]).total_seconds()
    with localcontext() as context:
        context.prec = 250
        difference = outgoing["amount"].copy_abs() - incoming["amount"]
        fee_aligned = (difference > 0 and outgoing["fee_asset"] == outgoing["asset"]
                       and difference == outgoing["fee"])
    amount_aligned = not difference or fee_aligned
    if not same_hash and (not amount_aligned or not -300 <= elapsed <= 7200):
        return None
    if same_hash and abs(elapsed) > 7 * 86_400:
        return None
    reason = ("These records share the same transaction ID, token and network." if same_hash
              else "These records show the same token and network, with similar amounts and times. There is no shared transaction ID to confirm the link.")
    if fee_aligned:
        unit = {"eip155:1/slip44:60": "ETH",
                "eip155:1/erc20:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
                "eip155:1/erc20:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
                "eip155:1/erc20:0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT"}.get(outgoing["asset"], "tokens")
        def amount(value):
            text = format(value, "f")
            return text.rstrip("0").rstrip(".") if "." in text else text
        amount_reason = (
            "You received less than you sent because a fee was deducted. "
            f"You sent {amount(outgoing['amount'].copy_abs())} {unit}, "
            f"paid a {amount(outgoing['fee'])} {unit} fee, "
            f"and received {amount(incoming['amount'])} {unit} in the wallet."
        )
        if not same_hash:
            amount_reason = "If these records are the same transfer: " + amount_reason[0].lower() + amount_reason[1:]
    elif difference:
        amount_reason = "The amounts differ, and the recorded fee does not explain why."
    else:
        amount_reason = "The amount sent matches the amount received."
    fields = ("ref", "account", "asset", "tx", "amount", "fee", "fee_asset", "at")
    def canonical(value):
        if isinstance(value, Decimal):
            raw = format(value, "f")
            return raw.rstrip("0").rstrip(".") if "." in raw else raw
        return str(value)

    identity = [[canonical(fact[field]) for field in fields] for fact in (outgoing, incoming)]
    digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    return {"id": digest, "outgoing": outgoing["row"]["id"], "incoming": incoming["row"]["id"],
            "confidence": "Hash match" if same_hash and amount_aligned else "Uncertain",
            "reasons": [reason, amount_reason], "amountDifference": format(difference, "f"),
            "minutesApart": round(elapsed / 60, 2)}


def suggestions(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("Narrow the transfer selection")
    facts = [_fact(row) for row in rows]
    counts = defaultdict(int)
    inbound = defaultdict(list)
    for fact in facts:
        if fact:
            counts[fact["ref"]] += 1
            if fact["amount"] > 0:
                inbound[fact["asset"]].append(fact)
    found = []
    for outgoing in facts:
        if outgoing is None or outgoing["amount"] > 0 or counts[outgoing["ref"]] != 1:
            continue
        for incoming in inbound[outgoing["asset"]]:
            if counts[incoming["ref"]] == 1:
                candidate = _pair(outgoing, incoming)
                if candidate is not None:
                    found.append(candidate)
    alternatives = Counter(ref for item in found for ref in (item["outgoing"], item["incoming"]))
    for item in found:
        if max(alternatives[item["outgoing"]], alternatives[item["incoming"]]) > 1:
            item["confidence"] = "Uncertain"
            item["reasons"].append("One of these records could match another transfer. Check the other suggestions before deciding.")
    found.sort(key=lambda item: (item["confidence"] != "Hash match", abs(item["minutesApart"]), item["id"]))
    return found[:MAX_SUGGESTIONS], len(found) > MAX_SUGGESTIONS, sum(
        fact is None or counts[fact["ref"]] != 1 for fact in facts)


def _view(item, decision):
    history, incomplete = trail(decision)
    return {**item, "status": decision.get("status", "pending"), "revision": decision.get("revision", 0),
            "updatedAt": decision.get("updatedAt"), "ownershipConfirmed": decision.get("status") == "accepted",
            "history": history, "historyIncomplete": incomplete}


def fixture_rows():
    from . import SAMPLE

    return [{**row, "originalId": row["id"], "id": f"{row['sourceMode']}:{row['id']}"}
            for row in json.loads(SAMPLE.read_text(encoding="utf-8"))["transfer_demo"]]


def session_fixture_review(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or any(
            not isinstance(row, dict) or not isinstance(row.get("at"), str) for row in rows):
        raise ValueError("Invalid fictional review selection")
    selected = {row.get("id") for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
    items, limited, skipped = suggestions([row for row in fixture_rows() if row["id"] in selected])
    return {"status": "ready", "items": [_view(item, {}) for item in items],
            "limited": limited, "skipped": skipped, "sessionOnly": True}


def _pinned(fact):
    row = fact["row"]
    record = {key: row[key] for key in ("id", "originalId", "sourceMode", "at", "asset",
                                         "quantity", "fee", "fee_asset", "kind", "evidence", "observations")}
    if (not isinstance(record["evidence"], list) or not isinstance(record["observations"], list)
            or len(json.dumps(record, ensure_ascii=True).encode()) > 24 * 1024):
        raise ValueError("Original transfer evidence exceeds the protected record limit")
    return record


def review(payload):
    if not isinstance(payload, dict):
        raise ValueError("Invalid transfer scope")
    mode, action = payload.get("mode"), payload.get("action")
    if mode == "combined-sample":
        if action != "list":
            raise ValueError("Fictional decisions stay in the Desktop session")
        return session_fixture_review(payload.get("rows"))
    if mode != "combined":
        raise ValueError("Invalid transfer scope")
    if action not in ("list", "accept", "reject", "undo"):
        raise ValueError("Invalid transfer action")
    snapshot, persist = protected_snapshot(MODE)
    snapshot = snapshot or {"mode": MODE, "decisions": {}}
    decisions = snapshot["decisions"]
    rows = payload.get("rows")
    if action == "list":
        items, limited, skipped = suggestions(rows)
        return {"status": "ready", "items": [_view(item, decisions.get(item["id"], {})) for item in items],
                "limited": limited, "skipped": skipped}
    if not isinstance(rows, list) or len(rows) != 2:
        raise ValueError("Choose both source records")
    outgoing, incoming = [_fact(row) for row in rows]
    item = _pair(outgoing, incoming) if outgoing and incoming else None
    if item is None or payload.get("id") != item["id"]:
        raise ValueError("Transfer evidence changed")
    previous = decisions.get(item["id"], {})
    expected = payload.get("revision")
    if type(expected) is not int or expected != previous.get("revision", 0):
        return {"status": "changed"}
    if action == "undo" and previous.get("status") not in ("accepted", "rejected"):
        return {"status": "changed"}
    if action in ("accept", "reject") and previous.get("status") in ("accepted", "rejected"):
        return {"status": "changed"}
    refs = {outgoing["ref"], incoming["ref"]}
    if action == "accept":
        if payload.get("owned") is not True:
            return {"status": "ownership-required"}
        if any(key != item["id"] and value["status"] == "accepted" and refs.intersection(value["refs"])
               for key, value in decisions.items()):
            return {"status": "already-linked"}
    if not previous and len(decisions) >= MAX_DECISIONS:
        return {"status": "capacity"}
    record = {"status": {"accept": "accepted", "reject": "rejected", "undo": "undone"}[action],
              "revision": expected + 1, "refs": sorted(refs), "updatedAt": datetime.now(timezone.utc).isoformat(),
              "sourceRecords": previous.get("sourceRecords") or [_pinned(outgoing), _pinned(incoming)]}
    decisions[item["id"]] = append_event(previous, record)
    persist(snapshot)
    return {"status": "ready", "item": _view(item, record)}
