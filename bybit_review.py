"""Bounded API-only Bybit Unified Spot review for the isolated Desktop."""
import csv
import hashlib
import io
import re
import time
from collections import Counter
from datetime import date, datetime, time as midnight, timezone
from decimal import localcontext

from .bybit import TOKEN, get_read_only, read_spot_activity
from .ledger import Entry, Ledger, number
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

MODE = "bybit-spot"
CSV_MODE = "bybit-csv"
COLUMNS = ("Spot Pairs", "Order Type", "Direction", "feeCoin", "ExecFeeV2", "Filled Value",
           "Filled Price", "Filled Quantity", "Fees", "Transaction ID", "Order No.", "Timestamp (UTC)")
CSV_GAPS = [
    "Coverage is limited to rows present in the supplied Bybit Spot Trade History CSV.",
    "No API comparison. Trade timestamps have minute precision in this export.",
    "The export order number was not verified against API order IDs.",
    "Transfers, derivatives, valuations and tax calculations were excluded.",
]
GAPS = [
    "Only Bybit Global Unified Spot executions returned for the selected UTC day are included.",
    "This is an API-only review. There was no CSV comparison.",
    "Transfers, legacy Spot archives, derivatives, valuations and tax calculations were excluded.",
]


def storage(*, csv_only=False):
    return protected_snapshot(CSV_MODE if csv_only else MODE)


def review(payload, key, secret, get=get_read_only):
    if (not isinstance(payload, dict) or set(payload) != {"date"}
            or not isinstance(payload["date"], str) or len(payload["date"]) != 10):
        raise ValueError("Choose one UTC date")
    day = date.fromisoformat(payload["date"])
    if not date(1970, 1, 1) <= day <= datetime.now(timezone.utc).date():
        raise ValueError("Choose a current or past UTC date")
    start = int(datetime.combine(day, midnight(), timezone.utc).timestamp()) * 1000
    entries, coverage = read_spot_activity(get, "review", start, start + 86_400_000 - 1,
                                           lambda: time.time_ns() // 1_000_000, key, secret)
    return _snapshot(entries, mode=MODE, start=day.isoformat(), end=day.isoformat(), coverage=coverage,
                     gaps=GAPS, source="Bybit API")


def read_export(text):
    if not isinstance(text, str) or not 0 < len(text.encode()) <= 1024 * 1024:
        raise ValueError("Choose a Bybit Spot CSV under 1 MiB")
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    digest = hashlib.sha256(text.encode()).hexdigest()
    entries = []
    try:
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError("Unsupported Bybit Spot Trade History columns")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Invalid Bybit Spot CSV row")
            pair, fee_asset, direction = row["Spot Pairs"], row["feeCoin"], row["Direction"]
            identity = row["Transaction ID"]
            if (not TOKEN.fullmatch(pair) or not TOKEN.fullmatch(fee_asset)
                    or direction not in ("BUY", "SELL") or not re.fullmatch(r"[0-9]{1,32}", identity)
                    or row["Fees"] not in ("", "--")):
                raise ValueError("Unsupported Bybit Spot CSV fill")
            time_text = row["Timestamp (UTC)"]
            at = datetime.strptime(time_text, "%H:%M %Y-%m-%d").replace(tzinfo=timezone.utc)
            if at.strftime("%H:%M %Y-%m-%d") != time_text:
                raise ValueError("Unsupported Bybit Spot CSV time")
            quantity, cost, fee = (number(row[name]) for name in ("Filled Quantity", "Filled Value", "ExecFeeV2"))
            if quantity <= 0 or cost < 0 or number(row["Filled Price"]) <= 0:
                raise ValueError("Invalid Bybit CSV fill quantities")
            key = f"bybit-global:csv:spot:{pair}:{identity}"
            evidence = f"csv:bybit-global:{digest}:{reader.line_num}"
            entries.append(Entry(key, at.isoformat(timespec="minutes"), direction.lower(), f"bybit-pair:{pair}",
                                 quantity, (evidence,), f"bybit-asset:{fee_asset}" if fee else "", fee, cost=cost,
                                 original=tuple(sorted(row.items()))))
    except csv.Error:
        raise ValueError("Invalid Bybit Spot CSV") from None
    if not entries:
        raise ValueError("This Bybit export has no Spot fills")
    return entries


def csv_review(payload):
    if not isinstance(payload, dict) or set(payload) != {"csv"}:
        raise ValueError("Choose a Bybit Spot CSV")
    entries = read_export(payload["csv"])
    dates = [row.at[:10] for row in entries]
    coverage = {"section": "spot-csv", "pages": 0, "failed_pages": [], "complete": False,
                "gap": "file-only", "unresolved_fees": []}
    return _snapshot(entries, mode=CSV_MODE, start=min(dates), end=max(dates), coverage=coverage,
                     gaps=CSV_GAPS, source="Bybit CSV")


def _snapshot(entries, *, mode, start, end, coverage, gaps, source):
    ledger = Ledger()
    for row in entries:
        ledger.add(row)
    conflicts = ledger.conflicts()
    if mode == CSV_MODE:
        for row in ledger.entries():
            facts = {item.original for item in ledger.observations(row.key)}
            if len(facts) > 1:
                conflicts[row.key] = len(facts)
    unresolved = set(coverage["unresolved_fees"]) | conflicts.keys()
    rows, fees, orders = [], {}, Counter()
    with localcontext() as context:
        context.prec = 250
        for row in ledger.entries():
            if row.key in conflicts:
                continue
            if row.related:
                orders[row.related] += 1
            if row.fee_asset and row.key not in unresolved:
                fees[row.fee_asset] = fees.get(row.fee_asset, 0) + row.fee
            rows.append({"id": row.key, "at": row.at, "kind": row.kind, "asset": row.asset,
                         "quantity": format(row.quantity, "f"), "fee": format(row.fee, "f"),
                         "fee_asset": row.fee_asset, "cost": format(row.cost, "f"),
                         "evidence": row.evidence,
                         "observations": [dict(item.original) for item in ledger.observations(row.key)]})
    return {"mode": mode, "start": start, "end": end, "source": source, "rows": rows,
            "coverage": [coverage], "gaps": gaps, "conflicts": conflicts, "unresolved": sorted(unresolved),
            "conflict_observations": {key: [dict(item.original) for item in ledger.observations(key)]
                                      for key in conflicts},
            "conflict_evidence": ledger.conflict_evidence(conflicts),
            **({"api_rows": len({row.key for row in entries}),
                "multi_fill_orders": sum(count > 1 for count in orders.values())}
               if mode == MODE else {"csv_rows": len(entries)}),
            "fee_totals": {asset: format(value, "f") for asset, value in sorted(fees.items())}}
