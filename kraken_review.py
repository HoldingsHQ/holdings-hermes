"""A bounded, local Kraken Spot review. No hosted model or log writes."""
import hashlib
import json
import time
from collections import Counter
from datetime import date, datetime, time as day_time, timedelta, timezone
from decimal import localcontext
from threading import Lock

from .kraken import post_read_only, read_export, read_history, reconcile, verify_query_permissions
from .storage_proof import ProtectedKeyUnavailable, private_folder, protected_context

MAX_DAYS = 31
PAGES = 3
MODE = "kraken-spot"
snapshot_lock = Lock()


def utc_window(start, end):
    if not isinstance(start, str) or not isinstance(end, str) or len(start) != 10 or len(end) != 10:
        raise ValueError("Use YYYY-MM-DD UTC dates")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days < MAX_DAYS:
        raise ValueError("Choose up to 31 UTC calendar days")
    epoch = lambda day: int(datetime.combine(day, day_time(), timezone.utc).timestamp())
    return epoch(first), epoch(last + timedelta(days=1))


def review(payload, api_key, private_key, post=post_read_only):
    if not isinstance(payload, dict):
        raise ValueError("Invalid review input")
    start_date, end_date = payload.get("start"), payload.get("end")
    start, end = utc_window(start_date, end_date)
    csv_entries = []
    for section, field in (("trades", "trades"), ("ledger", "ledgers")):
        text = payload.get(field)
        if not isinstance(text, str) or not text or len(text.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Each Kraken CSV must be at most 1 MiB")
        for row in read_export(text, section, "review"):
            if not start <= datetime.fromisoformat(row.at).timestamp() < end:
                raise ValueError("A CSV row is outside the selected UTC dates")
            csv_entries.append(row)
    previous = 0

    def next_nonce():
        nonlocal previous
        previous = max(previous + 1, time.time_ns() // 1000)
        return previous

    window = verify_query_permissions(post, next_nonce, api_key, private_key)
    if ((window["query_from"] is not None and start <= window["query_from"]) or
        (window["query_to"] is not None and end > window["query_to"])):
        raise ValueError("Selected dates exceed this Kraken key's query window")
    api_entries, coverage = [], []
    for method in ("TradesHistory", "Ledgers"):
        entries, bounds = read_history(post, method, "review", start - 1, end, next_nonce,
                                       api_key, private_key, max_pages=PAGES)
        api_entries.extend(row for row in entries if start <= datetime.fromisoformat(row.at).timestamp() < end)
        coverage.append(bounds)

    ledger = reconcile(api_entries, csv_entries)
    api_ids = {row.key for row in api_entries}
    csv_ids = {row.key for row in csv_entries}
    conflicts = ledger.conflicts()
    order_counts = Counter()
    rows = []
    fees = {}
    with localcontext() as context:
        context.prec = 250
        for row in ledger.entries():
            observations = ledger.observations(row.key)
            order_ids = {dict(item.original).get("ordertxid") for item in observations}
            order_ids.discard(None)
            order_ids.discard("")
            if row.kind in {"buy", "sell"} and len(order_ids) == 1:
                order_counts.update(order_ids)
            if row.fee_asset:
                fees[row.fee_asset] = fees.get(row.fee_asset, 0) + row.fee
            rows.append({"id": row.key, "at": row.at, "kind": row.kind, "asset": row.asset,
                         "quantity": format(row.quantity, "f"), "fee": format(row.fee, "f"),
                         "fee_asset": row.fee_asset, "cost": format(row.cost, "f"),
                         "evidence": row.evidence,
                         "observations": [dict(item.original) for item in observations]})
    unresolved = sorted(set(conflicts) | api_ids.symmetric_difference(csv_ids) |
                        {row["id"] for row in rows if row["kind"] == "unresolved"})
    return {"mode": MODE, "start": start_date, "end": end_date, "rows": rows,
            "coverage": coverage, "conflicts": conflicts, "unresolved": unresolved,
            "conflict_observations": {identity: [dict(item.original) for item in ledger.observations(identity)]
                                      for identity in conflicts},
            "conflict_evidence": ledger.conflict_evidence(conflicts),
            "overlap": len((api_ids & csv_ids) - conflicts.keys()),
            "api_rows": len(api_ids), "csv_rows": len(csv_ids),
            "multi_fill_orders": sum(count > 1 for count in order_counts.values()),
            "fee_totals": {asset: format(value, "f") for asset, value in sorted(fees.items())}}


def snapshot_location():
    home, key, scope = protected_context()
    folder = private_folder(home)
    path = folder / f"{MODE}.enc"
    if folder.is_symlink() or folder.stat().st_mode & 0o077 or path.is_symlink():
        raise ValueError("Protected storage path is not private")
    return path, key, scope


def _check_mode(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("mode") != MODE:
        raise ValueError("Wrong protected snapshot type")


def _open_snapshot(path, key, profile_scope, mode_scope, *, migrate):
    from .vault import VaultLocked, load, store

    try:
        data = load(path, key=key, scope=mode_scope)
        legacy = False
    except VaultLocked:
        data = load(path, key=key, scope=profile_scope)
        legacy = True
    snapshot = json.loads(data)
    _check_mode(snapshot)
    if legacy and migrate:
        store(path, data, key=key, scope=mode_scope)
    return snapshot


def persist(snapshot):
    from .vault import store

    _check_mode(snapshot)
    data = json.dumps(snapshot, ensure_ascii=True, separators=(",", ":")).encode()
    with snapshot_lock:
        path, key, profile_scope = snapshot_location()
        mode_scope = hashlib.sha256(profile_scope + MODE.encode()).digest()
        if path.exists():
            _open_snapshot(path, key, profile_scope, mode_scope, migrate=False)
        store(path, data, key=key, scope=mode_scope)


def reopen():
    with snapshot_lock:
        path, key, profile_scope = snapshot_location()
        mode_scope = hashlib.sha256(profile_scope + MODE.encode()).digest()
        return _open_snapshot(path, key, profile_scope, mode_scope, migrate=True)
