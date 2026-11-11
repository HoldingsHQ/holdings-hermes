"""Public and explicitly opted-in protected projections for chat tools."""
import re
from decimal import Decimal, localcontext

from . import ethereum_review, market, performance, public_chain, token_prices, wallet_security, year_review
from .ledger import number
from .storage_proof import protected_snapshot

OPT_IN = "share_private_chat_tools"
ENABLE_NOTICE = "To share saved private portfolio data with chat, enable Allow private portfolio data in Holdings settings. Hosted models can receive tool outputs."
ADDRESS = re.compile(r"0x[0-9a-f]{40}(?![0-9a-f])", re.IGNORECASE)
SCOPES = ("fictional", "public", "private")
ROW_LIMIT = 12
HISTORY_MODES = ("kraken-spot", "binance-spot", "bybit-spot", "bybit-csv")


def _wallet(args):
    selection = public_chain.wallet_selection({"wallets": [{"address": args.get("address"),
        "chainId": args.get("chain_id", 1)}]}, solana=True)
    return selection[0]


def _public_balances(topic, args, wallet):
    snapshot = public_chain.read_wallet_selection([wallet], None)["wallets"][0]
    ref = f'{snapshot["chain"]} · {snapshot.get("source", "public RPC")} · ' + str(
        snapshot.get("block") or snapshot.get("slot") or snapshot.get("fetchedAt") or "current request")
    if snapshot.get("status") != "ready":
        return {"status": "unavailable", "evidence": [ref]}
    if topic == "needs_attention":
        contracts = [row["contract"] for row in snapshot["balances"]
                     if row.get("contract") and row.get("units") and number(row["units"]) > 0][:32]
        result = wallet_security.snapshot([wallet], [contracts])["wallets"][0]
        return {"checks": [{"check": name, "coverage": check["state"], "findings": [
            {"title": item["title"], "evidence": item["id"]} for item in check["findings"]]}
            for name, check in result.items()], "evidence": [ref, "GoPlus wallet security"],
            "limitations": ["Only this public wallet and supported GoPlus checks. No saved alerts were read."]}
    balances = [{"asset": row["asset"], "units": row["units"], "evidence": ref} for row in snapshot["balances"]]
    if topic == "exposure":
        balances = [row for row in balances if row["asset"].casefold() == args["asset"].casefold()]
    quote = market.pool_snapshot() if wallet[1] == 1 else {}
    observations = performance.wallet_points([wallet], [snapshot], quote, token_prices.current([wallet]))
    priced = next(iter(observations.values()), {})
    priced_value = priced.get("valueUsd")
    changes = quote.get("marketChanges") if priced_value is not None and not quote.get("stale") else None
    return {"asOf": snapshot["fetchedAt"], "chain": snapshot["chain"], "balances": balances[:ROW_LIMIT],
            "pricedSubsetUsd": priced_value if topic == "summary" else asset_value(priced.get("lines", []), args["asset"]),
            "topMovers": None, "publicWethChange": changes,
            "evidence": [ref, *[f'{leg["provider"]} · {leg["quoteAt"]}' for leg in priced.get("lines", [])]],
            "limitations": ["Wallet balance coverage is bounded. No portfolio total, allocations or asset-wide ranking is established.",
                            "Only the curated keyless tokens are priced here, using DefiLlama prices with at least 0.9 confidence from the last hour. Without one, Ethereum WETH and ETH use the WETH/USD pool rate and canonical USDC counts at $1."]}


def _project_rows(rows):
    keys = ("id", "source", "at", "kind", "asset", "quantity", "fee", "fee_asset", "evidence")
    return [{key: row[key] for key in keys if key in row} for row in rows[:ROW_LIMIT]]


def _public_history(topic, args, wallet, key):
    start, end = args.get("start_block"), args.get("end_block")
    if wallet[1] != 1 or type(start) is not int or type(end) is not int or not 0 <= start <= end or end - start > 10_000:
        return {"status": "unavailable", "limitations": ["Supply Ethereum mainnet and an inclusive range of at most 10,001 blocks."]}
    if not key:
        return {"status": "unavailable", "limitations": ["Enable the Etherscan key in Holdings settings for public Ethereum history."]}
    snapshot = ethereum_review.review(wallet[0], start, end, key)
    selection = {**snapshot, "source": "Ethereum public block scan"}
    report = _history(topic, args, [selection])
    return {**report, "requestedBlocks": [start, end], "limitations": snapshot["gaps"] + report["limitations"]}


def _history(topic, args, snapshots):
    coverage = [{"source": snapshot["source"], "from": snapshot["start"], "through": snapshot["end"],
                 "streams": [{key: row[key] for key in ("section", "pages", "complete", "failed_pages") if key in row}
                             for row in snapshot.get("coverage", [])],
                 "evidence": snapshot["source"] + " · requested coverage"} for snapshot in snapshots]
    rows = [{**row, "source": snapshot["source"]} for snapshot in snapshots for row in snapshot["rows"]
            if row["kind"] != "unresolved" and row["id"] not in snapshot.get("conflicts", {})]
    if topic == "recent_movements":
        selected = sorted((row for row in rows if row["at"][:10] >= args["since"]),
                          key=lambda row: (row["at"], row["id"]), reverse=True)
        return {"recordCount": len(selected), "rows": _project_rows(selected), "coverage": coverage,
                "limitations": ["At most 12 cited records. Transfers can have separate source legs."]}
    selected = [row for row in rows if row["at"][:4] == str(args["year"])]
    totals = year_review.recorded_flows([{**row, "sourceMode": row["source"]} for row in selected])[:ROW_LIMIT]
    totals = [{**row, "evidence": row["evidence"][:5]} for row in totals]
    return {"requestedYearUTC": args["year"], "recordCount": len(selected), "flows": totals, "coverage": coverage,
            "limitations": ["Bounded source records with separate legs. No linked-transfer adjustment, annual completeness or tax calculation.",
                            "At most 12 source and asset groups are shown."]}


def public_report(topic, args, *, get_key):
    try:
        wallet = _wallet(args)
        if topic in {"summary", "exposure", "needs_attention"}:
            return _public_balances(topic, args, wallet)
        return _public_history(topic, args, wallet, get_key())
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "unavailable", "limitations": ["Check the public wallet, network and bounded block selection."]}


def asset_value(lines, asset):
    rows = [row for row in lines if str(row.get("asset") or performance.priced_asset(row["identity"]) or "").upper() == str(asset).upper()]
    if not rows:
        return None
    with localcontext() as context:
        context.prec = 100
        return format(sum((number(row["units"]) * number(row["price"]) for row in rows), Decimal(0)), "f")


def _private_value(topic, args):
    saved, _ = protected_snapshot(performance.MODE)
    latest = {}
    for stream in (saved or {}).get("streams", {}).values():
        point = stream[-1]
        name = point["source"]
        if name not in latest or latest[name]["at"] < point["at"]:
            latest[name] = point
    reports = []
    for source, point in sorted(latest.items()):
        lines = [row for row in point["lines"] if topic == "summary"
                 or args["asset"].upper() == str(row.get("asset") or performance.priced_asset(row["identity"]) or "").upper()]
        with localcontext() as context:
            context.prec = 100
            value = format(sum((number(row["units"]) * number(row["price"]) for row in lines), Decimal(0)), "f") if lines else None
        reports.append({"source": source, "at": point["at"], "pricedRows": len(lines), "observedRows": point["observed"],
                        "pricedSubsetUsd": value, "evidence": f"{source} · encrypted Holdings refresh · {point['at']}"})
    return {"sources": reports, "total": None, "allocation": None, "topMovers": None,
            "limitations": ["Latest saved priced subset per source. Different scopes and incomplete pricing prevent a combined total."]}


def private_report(topic, args):
    try:
        if topic in {"summary", "exposure"}:
            return _private_value(topic, args)
        if topic == "needs_attention":
            saved, _ = protected_snapshot("briefing-v1")
            items = [{"kind": kind, "count": len(review["items"]),
                      "evidence": f'Holdings {kind} review · {review["at"]}'}
                     for kind, review in (saved or {}).get("attention", {}).items() if review["items"]]
            return {"items": items, "limitations": ["Latest saved transfer and conflict queue counts."]}

        snapshots, unavailable = [], []
        for source in HISTORY_MODES:
            try:
                snapshot, _ = protected_snapshot(source)
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                snapshot = None
            if snapshot is None:
                unavailable.append(source)
            else:
                snapshots.append({**snapshot, "source": source})
        return {**_history(topic, args, snapshots), "unavailableSources": unavailable}
    except (ImportError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "unavailable", "limitations": ["Saved protected holdings data is unavailable in this profile."]}
