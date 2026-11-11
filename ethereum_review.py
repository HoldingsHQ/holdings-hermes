"""Bounded wallet observations and attributed swap legs for Desktop."""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from .ethereum import ADDR, ROUTERS, TX_HASH, inspect_swap
from .ledger import as_units
from .public_chain import CHAIN, factory_rpc, fetch_history, rpc

ETH = "eip155:1/slip44:60"
GAPS = [
    "Only normal transactions and ERC-20 transfer observations in this block window were queried.",
    "Swap proof covers up to two direct, wallet-funded USDC/WETH Uniswap v3 candidates.",
    "Internal transfers, NFTs, other protocols and historical valuations were excluded.",
]


def integer(value, limit=78):
    if not isinstance(value, (int, str)) or isinstance(value, bool):
        raise ValueError("Expected a nonnegative integer")
    raw = str(value)
    if not raw or len(raw) > limit or not raw.isascii() or not raw.isdecimal():
        raise ValueError("Expected a nonnegative integer")
    return int(raw)


def observation_id(stream, tx, ordinal):
    return f"etherscan:{stream}:{tx}:{ordinal}"


def review(wallet, start, end, key, *, transaction="", history=fetch_history, verify=None):
    if not isinstance(wallet, str) or not ADDR.fullmatch(wallet.lower()):
        raise ValueError("Enter a valid Ethereum address")
    wallet = wallet.lower()
    if not isinstance(transaction, str) or (transaction and not TX_HASH.fullmatch(transaction.lower())):
        raise ValueError("Enter a valid optional transaction hash")
    transaction = transaction.lower()
    start, end = integer(start, 10), integer(end, 10)
    raw, coverage = history(wallet, start, end, key, 25, 3, timeout=4)
    if verify is None:
        def verify(address, tx):
            return inspect_swap(rpc(CHAIN, timeout=3), factory_rpc(timeout=3), address, tx)

    rows, unresolved = [], set()
    token_rows = defaultdict(list)
    occurrences = Counter()
    for index, item in enumerate(raw["tokentx"], 1):
        tx = item["hash"].lower()
        identity = observation_id("tokentx", tx, index)
        token_rows[tx].append((identity, item))
        fingerprint = tuple(item.get(field) for field in
                            ("contractAddress", "from", "to", "value", "logIndex"))
        occurrences[(tx, fingerprint)] += 1

    def row(identity, item, kind, asset, quantity, *, refs=(), observed=None, fee=Decimal(0), transfer_ref=None):
        at = datetime.fromtimestamp(integer(item.get("timeStamp"), 12), timezone.utc).isoformat()
        rows.append({
            "id": identity, "at": at, "kind": kind, "asset": asset,
            "quantity": format(quantity, "f"), "fee": format(fee, "f"),
            "fee_asset": ETH if fee else "", "cost": "0",
            "evidence": [identity, *refs], "observations": observed if observed is not None else [item],
            **({"transfer": {"asset": asset, "hash": item["hash"].lower(), "ref": transfer_ref,
                             "account": f"eip155:1:{wallet}"}} if transfer_ref else {}),
        })

    verified = set()
    eligible = list(dict.fromkeys(item["hash"].lower() for item in raw["txlist"]
                                  if (item.get("from") or "").lower() == wallet
                                  and (item.get("to") or "").lower() in ROUTERS))
    if transaction in eligible:
        eligible.remove(transaction)
        eligible.insert(0, transaction)
    candidates = set(eligible[:2])
    unavailable = 0
    transactions = set()
    with localcontext() as context:
        context.prec = 250
        for index, item in enumerate(raw["txlist"], 1):
            tx = item["hash"].lower()
            identity = observation_id("txlist", tx, index)
            if tx in transactions:
                unresolved.add(identity)
                continue
            transactions.add(tx)
            sender = (item.get("from") or "").lower()
            target = (item.get("to") or "").lower()
            try:
                fee = (as_units(integer(item["gasUsed"]) * integer(item["gasPrice"]), 18)
                       if sender == wallet else Decimal(0))
                if tx in candidates:
                    proof = verify(wallet, tx)
                    unavailable += proof["status"] == "unavailable"
                    if (proof["status"] == "verified" and proof.get("block") == integer(item["blockNumber"])
                        and proof.get("transaction") == tx and proof.get("wallet") == wallet):
                        verified.add(tx)
                        row(identity, item, "network_fee", ETH, Decimal(0), fee=fee)
                        associated = token_rows[tx]
                        for leg in proof["rows"]:
                            rows.append({**leg, "fee_asset": "", "fee": "0", "cost": "0",
                                         "evidence": [*leg["evidence"], identity, *(ref for ref, _ in associated)],
                                         "observations": [item, *(event for _, event in associated)]})
                        if len(associated) != len(proof["rows"]):
                            unresolved.update(ref for ref, _ in associated)
                        continue
                successful_transfer = item.get("isError") == "0" and item.get("input") == "0x"
                if (successful_transfer and sender != target and wallet in {sender, target}
                        and ADDR.fullmatch(sender) and ADDR.fullmatch(target)):
                    value = integer(item["value"])
                    row(identity, item, "transfer", ETH, as_units(-value if sender == wallet else value, 18),
                        fee=fee, transfer_ref=f"{wallet}:{tx}:native")
                else:
                    unresolved.add(identity)
                    row(identity, item, "unresolved", ETH, Decimal(0), fee=fee)
            except (KeyError, OverflowError, TypeError, ValueError):
                unresolved.add(identity)

        for tx, observed in token_rows.items():
            if tx in verified:
                continue
            for identity, item in observed:
                fingerprint = tuple(item.get(field) for field in
                                    ("contractAddress", "from", "to", "value", "logIndex"))
                if occurrences[(tx, fingerprint)] != 1:
                    unresolved.add(identity)
                    continue
                try:
                    contract = item["contractAddress"].lower()
                    sender, target = item["from"].lower(), item["to"].lower()
                    decimals = integer(item["tokenDecimal"], 2)
                    if (not all(ADDR.fullmatch(address) for address in (contract, sender, target))
                        or decimals > 36 or sender == target or wallet not in {sender, target}):
                        raise ValueError("Unverified token observation")
                    value = integer(item["value"])
                    marker = hashlib.sha256(json.dumps([contract, sender, target, value, item.get("logIndex")],
                                                       separators=(",", ":")).encode()).hexdigest()
                    row(identity, item, "token_transfer", f"eip155:1/erc20:{contract}",
                        as_units(-value if sender == wallet else value, decimals),
                        transfer_ref=f"{wallet}:{tx}:{marker}")
                except (KeyError, OverflowError, TypeError, ValueError):
                    unresolved.add(identity)
        fee_total = sum((Decimal(item["fee"]) for item in rows), Decimal(0))

    return {"mode": "ethereum", "wallet": wallet, "start": start, "end": end, "rows": rows,
            "coverage": [{"section": name, "complete": part["end_of_results"], **part}
                         for name, part in coverage["streams"].items()],
            "unresolved": sorted(unresolved),
            "unresolved_observations": {
                observation_id(stream, item["hash"].lower(), index): item
                for stream, events in raw.items() for index, item in enumerate(events, 1)
                if observation_id(stream, item["hash"].lower(), index) in unresolved
            },
            "conflicts": {}, "gaps": GAPS,
            "verified_swaps": len(verified), "swap_candidates": len(candidates),
            "swap_unavailable": unavailable, "requested_swap_in_scope": transaction in candidates if transaction else None,
            "fee_totals": {ETH: format(fee_total, "f")} if fee_total else {}}
