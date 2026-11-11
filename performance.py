"""Protected, source-scoped valuation observations. No cash-flow or return estimates."""
import hashlib
import json
from decimal import Decimal, localcontext

from .ledger import number
from .market import WETH
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

MODE = "performance-v1"
# A full read of 12 pairs records 13 streams (each pair plus the combined total), so keep two full reads.
MAX_SCOPES = 26
MAX_POINTS = 60
MAX_POSITION_NAMES = 32


def scope_id(parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def priced_leg(identity, units, market, provider):
    if market.get("status") != "ready" or market.get("stale") or not market.get("candles"):
        return None
    price = number(market["candles"][-1][4])
    if price <= 0:
        return None
    return {"identity": identity, "units": units, "price": format(price, "f"),
            "quoteAt": market["closedAt"], "provider": provider}


# Canonical Circle USDC contracts per chain, matching public_chain.WALLET_NETWORKS. Bridged or
# pegged variants (for example Binance-Peg USDC) and other stablecoins stay unpriced.
USDC_CONTRACTS = {
    1: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    8453: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    42161: "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
}
ETH_AT_WETH = "GeckoTerminal WETH/USD (ETH priced at the WETH rate)"
USDC_AT_PAR = "Fixed USDC/USD (USDC counted at $1)"
LLAMA = "DefiLlama price"


def par_leg(identity, units, at):
    return {"identity": identity, "units": units, "price": "1", "quoteAt": at, "provider": USDC_AT_PAR}


def wallet_lines(pair, rows, market, stamp, prices=None):
    """Exact identities only. A curated row uses its DefiLlama price when one qualifies. Without it,
    mainnet WETH and native ETH fall back to the mainnet WETH/USD pool close and canonical USDC to $1.
    Every other row stays unpriced."""
    lines = []
    chain = pair[1]
    for row in rows:
        if row.get("units") is None or "contract" not in row:
            continue
        contract = (row.get("contract") or "").lower() or None
        # Earlier releases used these three suffixes, so saved points keep comparing cleanly.
        if chain == 1 and contract == WETH:
            suffix = "weth"
        elif contract is None and row.get("asset") == "ETH":
            suffix = "eth"
        elif contract is not None and USDC_CONTRACTS.get(chain) == contract:
            suffix = "usdc"
        else:
            suffix = contract or "native"
        identity = scope_id(pair) + ":" + suffix
        quote = (prices or {}).get((chain, contract))
        if quote is not None:
            leg = {"identity": identity, "units": row["units"], "price": quote["price"],
                   "quoteAt": quote["at"], "provider": LLAMA}
        elif chain == 1 and contract == WETH:
            leg = priced_leg(identity, row["units"], market, "GeckoTerminal WETH/USD")
        elif chain == 1 and contract is None and row.get("asset") == "ETH":
            leg = priced_leg(identity, row["units"], market, ETH_AT_WETH)
        elif contract is not None and USDC_CONTRACTS.get(chain) == contract:
            leg = par_leg(identity, row["units"], stamp)
        else:
            leg = None
        if leg is not None:
            lines.append({**leg, "chainId": chain, "asset": row.get("asset")})
    return lines


def observed_positions(rows, namespace, units):
    positions = []
    for row in rows:
        if units(row) > 0:
            positions.append([scope_id([namespace, row.get("contract"), row["asset"]]), row["asset"][:60]])
            if len(positions) > MAX_POSITION_NAMES:
                return None
    return positions


def point(at, lines, observed, complete, *, source="Public wallets", permissions=None, positions=None):
    with localcontext() as context:
        context.prec = 100
        value = sum((number(row["units"]) * number(row["price"]) for row in lines), Decimal(0))
    return {"at": at, "lines": lines, "observed": observed, "complete": complete, "source": source, "permissions": permissions,
            "valueUsd": format(value, "f") if lines else None, "positions": positions}


def wallet_points(selection, snapshots, market, prices=None):
    entries = {}
    combined = []
    for pair, snapshot in zip(selection, snapshots):
        if snapshot.get("status") != "ready":
            continue
        stamp = snapshot["fetchedAt"]
        rows = snapshot["balances"]
        lines = wallet_lines(pair, rows, market, stamp, prices)
        complete = (all(row["units"] is not None for row in rows)
                    and all(status == "ready" for status in snapshot.get("coverage", {}).values()))
        positions = observed_positions(rows, scope_id(pair), lambda row: number(row["units"])) if complete else None
        entry = point(stamp, lines, len(rows), complete, positions=positions)
        entries[scope_id([pair])] = entry
        combined.append(entry)
    if len(selection) > 1 and combined:
        lines = [row for entry in combined for row in entry["lines"]]
        complete = len(combined) == len(selection) and all(entry["complete"] for entry in combined)
        positions = None
        if complete and all(entry["positions"] is not None for entry in combined):
            available = [row for entry in combined for row in entry["positions"]]
            if len(available) <= MAX_POSITION_NAMES:
                positions = available
        entries[scope_id(sorted(selection))] = point(
            max(entry["at"] for entry in combined), lines, sum(entry["observed"] for entry in combined),
            complete, positions=positions)
    return entries


def exchange_point(exchange, result, market, prices=None):
    rows = result["balances"]
    lines = []
    for row in rows:
        quote = (prices or {}).get(row["asset"])
        if quote is None or row["free"] is None or row["locked"] is None:
            continue
        with localcontext() as context:
            context.prec = 100
            units = format(number(row["free"]) + number(row["locked"]), "f")
        lines.append({"identity": f"{exchange}:{row['asset']}", "units": units, "price": quote["price"],
                      "quoteAt": quote["at"], "provider": quote["provider"], "asset": row["asset"]})
    if exchange == "kraken" and not any(line["asset"] == "XXBT" for line in lines):
        # Kraken's canonical spot BTC code, against Kraken's own BTC/USD closes.
        btc = next((row for row in rows if row["asset"] == "XXBT" and row["free"] is not None), None)
        if btc:
            with localcontext() as context:
                context.prec = 100
                units = format(number(btc["free"]) + number(btc["locked"]), "f")
            leg = priced_leg("kraken:XXBT", units, market, "Kraken BTC/USD")
            if leg is not None:
                lines.append({**leg, "asset": "XXBT"})
    permissions = result.get("permissionStatus")
    source = {"kraken": "Kraken Spot", "bybit": "Bybit Spot", "binance": "Binance Spot"}[exchange]
    complete = all(row["free"] is not None and row["locked"] is not None for row in rows)
    with localcontext() as context:
        context.prec = 100
        positions = observed_positions(rows, source, lambda row: number(row["free"]) + number(row["locked"])) if complete else None
    return point(result["fetchedAt"], lines, len(rows), complete, source=source, permissions=permissions, positions=positions)


def explain(previous, latest):
    before = {row["identity"]: row for row in previous["lines"]}
    after = {row["identity"]: row for row in latest["lines"]}
    if (not before or before.keys() != after.keys() or not previous["complete"] or not latest["complete"]
            or previous["observed"] != latest["observed"]):
        return None
    with localcontext() as context:
        context.prec = 100
        market, quantity = Decimal(0), Decimal(0)
        for identity, old in before.items():
            new = after[identity]
            market += number(old["units"]) * (number(new["price"]) - number(old["price"]))
            quantity += number(new["price"]) * (number(new["units"]) - number(old["units"]))
    return {"from": previous["at"], "to": latest["at"],
            "marketUsd": format(market, "f"), "unitChangeUsd": format(quantity, "f")}


def priced_asset(identity):
    suffix = identity.rsplit(":", 1)[-1] if ":" in identity else ""
    return {"weth": "WETH", "eth": "ETH", "usdc": "USDC", "XXBT": "BTC"}.get(suffix)


def summary(points, status):
    return {"status": status, "points": [{**{key: item[key] for key in ("at", "observed", "complete", "valueUsd")},
        "prices": [{"provider": line["provider"], "quoteAt": line["quoteAt"], "asset": line.get("asset") or priced_asset(line["identity"]),
                    "chainId": line.get("chainId"), "price": line["price"]}
                   for line in item["lines"]]} for item in points],
        "change": explain(points[-2], points[-1]) if len(points) > 1 else None}


def record(entries, open_store=protected_snapshot):
    if not entries:
        return {}
    try:
        saved, persist = open_store(MODE)
        streams = {} if saved is None else saved["streams"]
        # An authenticated envelope does not guarantee a supported application schema.
        if not isinstance(streams, dict):
            raise ValueError("Invalid history format")
        updated = {}
        for key, entry in entries.items():
            points = [item for item in streams.get(key, []) if item["at"] != entry["at"]]
            points = sorted([*points, entry], key=lambda item: item["at"])[-MAX_POINTS:]
            updated[key] = points
        streams.update(updated)
        keep = sorted(streams, key=lambda key: streams[key][-1]["at"], reverse=True)[:MAX_SCOPES]
        views = {key: summary(points, "saved") for key, points in updated.items()}
        persist({"mode": MODE, "streams": {key: streams[key] for key in keep}})
        return views
    except ProtectedKeyUnavailable as exc:
        return {key: summary([entry], exc.status) for key, entry in entries.items()}
    except (ImportError, KeyError, IndexError, OSError, RuntimeError, TypeError, ValueError):
        return {key: summary([entry], "storage-locked") for key, entry in entries.items()}


def import_basis(content):
    """Summarize operator-supplied lots, without deriving them from trades."""
    import csv
    import io
    import re
    from datetime import date

    fields = ["source", "asset_id", "acquired_utc", "quantity", "cost_usd", "evidence_id"]
    reader = csv.DictReader(io.StringIO(content.removeprefix("\ufeff")), strict=True)
    if reader.fieldnames != fields:
        raise ValueError("Unexpected cost-basis columns")
    groups, seen = {}, set()
    with localcontext() as context:
        context.prec = 100
        for index, row in enumerate(reader):
            if index >= 100 or set(row) != set(fields):
                raise ValueError("Too many or invalid lots")
            source, asset, acquired, quantity, cost, evidence = (row[key] for key in fields)
            if (not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", source)
                    or not isinstance(asset, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:/._-]{0,120}", asset)
                    or not isinstance(evidence, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:/._-]{0,120}", evidence)
                    or not isinstance(acquired, str) or date.fromisoformat(acquired).isoformat() != acquired):
                raise ValueError("Invalid lot evidence")
            reference = (source, evidence)
            if reference in seen:
                raise ValueError("Duplicate lot evidence")
            seen.add(reference)
            quantity, cost = number(quantity), number(cost)
            if quantity <= 0 or cost < 0:
                raise ValueError("Invalid lot quantity or cost")
            group = groups.setdefault((source, asset), {"quantity": Decimal(0), "costUsd": Decimal(0), "lots": []})
            group["quantity"] += quantity
            group["costUsd"] += cost
            group["lots"].append({"id": evidence, "date": acquired, "quantity": format(quantity, "f"), "costUsd": format(cost, "f")})
    if not seen:
        raise ValueError("No lots")
    return [{"source": source, "asset": asset, "lots": group["lots"],
             "quantity": format(group["quantity"], "f"), "costUsd": format(group["costUsd"], "f")}
            for (source, asset), group in sorted(groups.items())]
