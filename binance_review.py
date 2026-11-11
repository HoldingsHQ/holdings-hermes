"""One bounded Binance.com Spot review from an explicitly selected local export."""
import csv
import hashlib
import io
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import localcontext

from .binance import ASSET, DAY_MS, get_read_only, read_spot_fills
from .ledger import Ledger, number
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

COLUMNS = ["Order No", "Time", "Pair", "Side", "Price", "Executed", "Amount", "Fee",
           "AOR Conversion Pair", "AOR Conversion Rate"]
# A suffix starting with a digit cannot be separated reliably from the quantity.
UNITS = re.compile(r"(\d+(?:\.\d+)?)([A-Z][A-Z0-9]{0,31})")
OFFSET = re.compile(r"([+-])(0[0-9]|1[0-4]):([0-5][0-9])")
GAPS = [
    "Only one Binance.com Spot pair and UTC day were queried.",
    "CSV rows are evidence only. Rows without one exact, unique API match stay unresolved.",
    "Transfers, derivatives, historical valuations and tax calculations were excluded.",
]


def _unit(value):
    match = UNITS.fullmatch(value)
    if match is None:
        raise ValueError("Unsupported Binance CSV quantity")
    return number(match[1]), match[2]


def _export(text, offset):
    if not isinstance(text, str) or not 0 < len(text.encode()) <= 1024 * 1024:
        raise ValueError("Choose a Binance Spot CSV no larger than 1 MiB")
    match = OFFSET.fullmatch(offset) if isinstance(offset, str) else None
    if match is None or (match[2] == "14" and match[3] != "00"):
        raise ValueError("Use the UTC offset shown when exporting the file")
    minutes = (int(match[2]) * 60 + int(match[3])) * (-1 if match[1] == "-" else 1)
    zone = timezone(timedelta(minutes=minutes))
    digest = hashlib.sha256(text.encode()).hexdigest()
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True)
    if reader.fieldnames != COLUMNS:
        raise ValueError("Unsupported Binance Spot Trade History columns")
    items = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Invalid Binance CSV row")
        at = datetime.strptime(row["Time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone).astimezone(timezone.utc)
        pair, side, order = row["Pair"], row["Side"].lower(), row["Order No"]
        quantity, base = _unit(row["Executed"])
        cost, quote = _unit(row["Amount"])
        fee, fee_asset = _unit(row["Fee"])
        price = number(row["Price"])
        if (not ASSET.fullmatch(pair) or pair != base + quote or base == quote
                or not re.fullmatch(r"(0|[1-9][0-9]{0,18})", order)
                or side not in {"buy", "sell"} or quantity <= 0 or price <= 0):
            raise ValueError("Unsupported Binance CSV fill")
        fingerprint = (order, at.isoformat(timespec="seconds"), side, quantity, cost, price, fee, fee_asset)
        items.append((at, pair, base, quote, f"csv:binance-global:{digest}:{reader.line_num}", fingerprint, row))
    if not items:
        raise ValueError("The Binance export has no Spot fills")
    return items


def _fingerprint(row):
    original = dict(row.original)
    at = datetime.fromisoformat(row.at).isoformat(timespec="seconds")
    return (original["orderId"], at, row.kind, row.quantity, row.cost,
            number(original["price"]), row.fee, original["commissionAsset"])


def review(payload, api_key, secret, get=get_read_only):
    if not isinstance(payload, dict) or set(payload) != {"csv", "offset"}:
        raise ValueError("Invalid Binance review input")
    try:
        exported = _export(payload["csv"], payload["offset"])
    except (csv.Error, OverflowError):
        raise ValueError("Invalid Binance export") from None
    latest = max(exported, key=lambda item: (item[0], item[1]))
    at, symbol, base, quote = latest[:4]
    date = at.date().isoformat()
    start = int(at.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()) * 1000
    selected = [item for item in exported if item[0].date() == at.date() and item[1:4] == latest[1:4]]
    api, coverage = read_spot_fills(get, "review", symbol, base, quote, start, start + DAY_MS - 1,
                                   lambda: time.time_ns() // 1_000_000, api_key, secret)
    ledger = Ledger()
    for row in api:
        ledger.add(row)

    candidates, supplied = defaultdict(list), defaultdict(list)
    for item in selected:
        supplied[item[5]].append((item[4], item[6]))
    conflicts = ledger.conflicts()
    entries = list(ledger.entries())
    for row in entries:
        facts = {_fingerprint(item) for item in ledger.observations(row.key)}
        if len(facts) == 1:
            candidates[next(iter(facts))].append(row.key)
    matches = {}
    unmatched = {item[4]: item[6] for item in selected}
    if coverage["complete"] and not conflicts:
        for facts, original in supplied.items():
            if len(original) == 1 and len(candidates.get(facts, ())) == 1:
                identity, fields = original[0]
                matches[candidates[facts][0]] = (identity, fields)
                del unmatched[identity]

    rows, fees = [], {}
    with localcontext() as context:
        context.prec = 250
        for row in entries:
            refs = list(row.evidence)
            observed = [dict(item.original) for item in ledger.observations(row.key)]
            if row.key in matches:
                ref, fields = matches[row.key]
                refs.append(ref)
                observed.append(fields)
            if row.fee_asset:
                fees[row.fee_asset] = fees.get(row.fee_asset, 0) + row.fee
            rows.append({"id": row.key, "at": row.at, "kind": row.kind, "asset": row.asset,
                         "quantity": format(row.quantity, "f"), "fee": format(row.fee, "f"),
                         "fee_asset": row.fee_asset, "cost": format(row.cost, "f"),
                         "evidence": refs, "observations": observed})
    counts = Counter(row.related for row in entries)
    return {"mode": "binance-spot", "start": date, "end": date, "symbol": symbol,
            "rows": rows, "coverage": [coverage], "conflicts": conflicts, "gaps": GAPS,
            "unresolved": sorted({row.key for row in api if row.key not in matches} | unmatched.keys()),
            "unresolved_observations": unmatched,
            "conflict_observations": {key: [dict(item.original) for item in ledger.observations(key)]
                                      for key in conflicts},
            "conflict_evidence": ledger.conflict_evidence(conflicts),
            "overlap": len(matches), "api_rows": len({row.key for row in api}), "csv_rows": len(selected),
            "multi_fill_orders": sum(count > 1 for count in counts.values()),
            "fee_totals": {asset: format(value, "f") for asset, value in sorted(fees.items())}}


def storage():
    return protected_snapshot("binance-spot")
