"""A bounded, public DefiLlama incident feed. Wallet matching stays on the client."""
import hashlib
import time
from datetime import datetime, timezone

from .public_chain import fetch
from .zerion import label

URL = "https://api.llama.fi/hacks"
WINDOW = 30 * 86400
NETWORKS = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "arbitrum one": 42161,
            "bsc": 56, "bnb chain": 56, "robinhood": 4663, "robinhood chain": 4663, "solana": "solana"}
_cache = None


def recent(data, now):
    if not isinstance(data, list) or len(data) > 5000:
        raise ValueError("Incident feed unavailable")
    rows = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        date = item.get("date")
        name = label(item.get("name"), "")
        if type(date) is not int or not now - WINDOW <= date <= now or not name:
            continue
        chains = item.get("chain")
        chains = chains if isinstance(chains, list) else []
        names = list(dict.fromkeys(filter(None, (label(value, "") for value in chains[:16]))))
        ids = [NETWORKS[value.casefold()] for value in names if value.casefold() in NETWORKS]
        provider_id = label(item.get("defillamaId"), "")
        technique = label(item.get("technique"), "")
        key = hashlib.sha256(f"{name}:{date}:{provider_id}:{sorted(names)}:{technique}".encode()).hexdigest()
        rows[key] = {"id": key, "name": name, "networks": names, "chainIds": ids, "technique": technique,
                     "at": datetime.fromtimestamp(date, timezone.utc).isoformat(timespec="seconds")}
    incidents = sorted(rows.values(), key=lambda row: row["at"], reverse=True)
    return {"incidents": incidents[:30], "limited": len(incidents) > 30}


def snapshot():
    global _cache
    current = time.time()
    clock = time.monotonic()
    if _cache and clock - _cache[0] < 300:
        return _cache[1]
    try:
        data = recent(fetch(URL, timeout=6), int(current))
    except (OSError, ValueError, RecursionError):
        return {"status": "unavailable"}
    report = {"status": "ready", **data,
              "retrievedAt": datetime.fromtimestamp(current, timezone.utc).isoformat(timespec="seconds")}
    _cache = clock, report
    return report
