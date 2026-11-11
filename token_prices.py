"""Keyless DefiLlama prices for the curated wallet list only. One bounded request per refresh."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from . import public_chain
from .public_chain import WALLET_NETWORKS

URL = "https://coins.llama.fi/prices/current/"
PROVIDER = "DefiLlama price"
MIN_CONFIDENCE = Decimal("0.9")
MAX_AGE_SECONDS = 3600
# DefiLlama chain keys. Robinhood Chain has no DefiLlama key, so its balances stay unpriced.
CHAINS = {1: "ethereum", 8453: "base", 42161: "arbitrum", 56: "bsc"}
NATIVE = {1: "coingecko:ethereum", 8453: "coingecko:ethereum", 42161: "coingecko:ethereum", 56: "coingecko:binancecoin"}


def coin_key(chain_id, contract):
    """The DefiLlama key for a curated row, or None for anything outside the list."""
    if chain_id not in CHAINS:
        return None
    curated = {row[1] for row in WALLET_NETWORKS[chain_id][2]}
    if contract not in curated:
        return None
    return NATIVE[chain_id] if contract is None else f"{CHAINS[chain_id]}:{contract}"


def accepted(entry, now):
    """A usable price needs confidence of at least 0.9 and a timestamp from the last hour."""
    if not isinstance(entry, dict):
        return None
    price, confidence, stamp = entry.get("price"), entry.get("confidence"), entry.get("timestamp")
    if type(price) not in (int, float) or type(confidence) not in (int, float) or type(stamp) is not int:
        return None
    try:
        price, confidence = Decimal(str(price)), Decimal(str(confidence))
    except InvalidOperation:
        return None
    if not price.is_finite() or not Decimal("0") < price < Decimal("1e12") or confidence < MIN_CONFIDENCE:
        return None
    if not 0 <= now - stamp <= MAX_AGE_SECONDS:
        return None
    return {"price": format(price, "f"), "at": datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"),
            "confidence": format(confidence, "f")}


def current(selection, get=None, now=None):
    """Return {(chainId, contract): price} for curated rows in the selection. Failures return {}."""
    wanted = {}
    for _, chain_id in selection:
        if chain_id == "solana" or chain_id not in WALLET_NETWORKS:
            continue
        for _, contract, _ in WALLET_NETWORKS[chain_id][2]:
            key = coin_key(chain_id, contract)
            if key:
                wanted.setdefault(key, []).append((chain_id, contract))
    if not wanted:
        return {}
    try:
        data = (get or public_chain.fetch)(URL + ",".join(sorted(wanted)), timeout=6)
    except (OSError, ValueError):
        return {}
    coins = data.get("coins") if isinstance(data, dict) else None
    if not isinstance(coins, dict):
        return {}
    moment = int(datetime.now(timezone.utc).timestamp()) if now is None else now
    prices = {}
    for key, rows in wanted.items():
        price = accepted(coins.get(key), moment)
        if price:
            for row in rows:
                prices[row] = price
    return prices
