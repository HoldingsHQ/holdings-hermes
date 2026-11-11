"""Bounded, read-only public market data for explicitly supported markets."""

import json
import re
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from threading import Lock
from urllib.request import Request, build_opener

from .kraken import _NoRedirect
from .ledger import number

MARKET = "XXBTZUSD"
OHLC = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60"
TICKER = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
KRAKEN_PAIRS = {"XXBTZUSD": "XBTUSD", "SOLUSD": "SOLUSD", "BNBUSD": "BNBUSD"}
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
POOL_MARKET = f"eip155:1:{POOL}"
POOL_OHLC = (f"https://api.geckoterminal.com/api/v2/networks/eth/pools/{POOL}/ohlcv/hour"
             f"?aggregate=1&limit=170&currency=usd&token={WETH}")
RESPONSE_LIMIT = 128 * 1024
HOUR = 3600
KRAKEN_CATALOG = "https://api.kraken.com/0/public/AssetPairs?aclass_base=currency"
CATALOG_LIMIT = 4 * 1024 * 1024
BASE_SYMBOL = re.compile(r"[A-Z0-9]{1,16}")
PAIR_CODE = re.compile(r"[A-Z0-9]{2,24}")


def _fetch(url, *, limit=RESPONSE_LIMIT):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Holdings-Hermes/0.1"})
    with build_opener(_NoRedirect()).open(request, timeout=5) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Market response exceeds limit")
    return json.loads(raw, parse_float=Decimal)


def _pair(payload, *, candles=False, market=MARKET):
    expected = {market, "last"} if candles else {market}
    if payload["error"] != [] or set(payload["result"]) != expected:
        raise ValueError("Unexpected Kraken market")
    return payload["result"][market]


def _price(value):
    price = number(value)
    if not 0 < price <= 1_000_000_000:
        raise ValueError("Market price out of bounds")
    return price


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def read_market(fetch=_fetch, clock=time.time, *, market=MARKET):
    return _read_kraken_market(market, KRAKEN_PAIRS[market], market, fetch, clock)


def _read_kraken_market(market, pair, identity, fetch, clock):
    ohlc = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=60"
    ticker = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    rows = _pair(fetch(ohlc), candles=True, market=market)
    now = clock()
    if not isinstance(rows, list) or not 2 <= len(rows) <= 721:
        raise ValueError("Unexpected Kraken candle count")
    candles = []
    previous = 0
    # Kraken always includes the uncommitted candle last.
    for row in rows[-49:-1]:
        if not isinstance(row, list) or len(row) != 8:
            raise ValueError("Invalid Kraken candle")
        timestamp = row[0]
        if type(timestamp) is not int or timestamp % HOUR or not previous < timestamp <= now - HOUR:
            raise ValueError("Invalid Kraken candle time")
        start, high, low, close = map(_price, row[1:5])
        if not low <= min(start, close) <= max(start, close) <= high:
            raise ValueError("Invalid Kraken candle range")
        candles.append([_utc(timestamp), *(format(value, "f") for value in (start, high, low, close))])
        previous = timestamp
    try:
        last_trade = format(_price(_pair(fetch(ticker), market=market)["c"][0]), "f")
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        last_trade = None
    return {
        "status": "ready", "market": identity, "candles": candles,
        "lastTrade": last_trade, "fetchedAt": _utc(clock()),
        "closedAt": _utc(previous + HOUR), "stale": now - previous >= 3 * HOUR,
    }


def read_pool_market(fetch=_fetch, clock=time.time):
    # GeckoTerminal's candle response ID is opaque. The fixed URL binds it to the
    # chain and pool, and the metadata independently identifies both token contracts.
    payload = fetch(POOL_OHLC)
    meta = payload["meta"]
    if (meta["base"]["address"], meta["quote"]["address"]) != (WETH, USDC):
        raise ValueError("Unexpected GeckoTerminal pool tokens")
    rows = payload["data"]["attributes"]["ohlcv_list"]
    now = clock()
    if not isinstance(rows, list) or not 1 <= len(rows) <= 170:
        raise ValueError("Unexpected GeckoTerminal candle count")
    candles = []
    previous = 0
    for row in reversed(rows):
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("Invalid GeckoTerminal candle")
        timestamp = row[0]
        if type(timestamp) is not int or timestamp % HOUR or not previous < timestamp <= now:
            raise ValueError("Invalid GeckoTerminal candle time")
        previous = timestamp
        if timestamp + HOUR > now:
            continue
        start, high, low, close = map(_price, row[1:5])
        volume = number(row[5])
        if not low <= min(start, close) <= max(start, close) <= high or not 0 <= volume <= 10**15:
            raise ValueError("Invalid GeckoTerminal candle range or volume")
        candles.append((timestamp, [_utc(timestamp), *(format(value, "f")
                         for value in (start, high, low, close, volume))]))
    if not candles:
        raise ValueError("No completed GeckoTerminal candles")
    latest, latest_row = candles[-1]
    closes = {timestamp: _price(row[4]) for timestamp, row in candles}
    changes = {}
    with localcontext() as context:
        context.prec = 40
        for window, hours in (("24h", 24), ("7d", 168)):
            earlier = closes.get(latest - hours * HOUR)
            changes[window] = format(((_price(latest_row[4]) / earlier) - 1) * 100, ".4f") if earlier else None
    return {
        "status": "ready", "market": POOL_MARKET,
        "candles": [row for timestamp, row in candles if timestamp > latest - 48 * HOUR],
        "marketChanges": changes,
        "fetchedAt": _utc(clock()), "closedAt": _utc(latest + HOUR),
        "stale": now - latest >= 3 * HOUR,
    }


def _throttled(read, *, success_ttl=60):
    """Throttle successful and failed fetches independently for each market."""
    lock = Lock()
    cached = None
    expires = 0

    def snapshot():
        nonlocal cached, expires
        with lock:
            if time.monotonic() < expires:
                return cached
            try:
                cached = read()
            except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
                cached = {"status": "unavailable"}
            expires = time.monotonic() + (success_ttl if cached["status"] == "ready" else 15)
            return cached

    return snapshot


snapshot = _throttled(read_market)
sol_snapshot = _throttled(lambda: read_market(market="SOLUSD"))
bnb_snapshot = _throttled(lambda: read_market(market="BNBUSD"))
pool_snapshot = _throttled(read_pool_market)


def read_catalog(fetch=None):
    body = fetch(KRAKEN_CATALOG) if fetch else _fetch(KRAKEN_CATALOG, limit=CATALOG_LIMIT)
    if not isinstance(body, dict) or body.get("error") != [] or not isinstance(body.get("result"), dict):
        raise ValueError("Invalid Kraken catalog")
    markets, ambiguous = {}, set()
    for code, pair in body["result"].items():
        if not isinstance(pair, dict) or not PAIR_CODE.fullmatch(code):
            continue
        if pair.get("status") != "online" or pair.get("quote") not in {"ZUSD", "USD"}:
            continue
        name, alias = pair.get("wsname"), pair.get("altname")
        if not isinstance(name, str) or not isinstance(alias, str) or not PAIR_CODE.fullmatch(alias):
            continue
        base, sep, currency = name.partition("/")
        if not sep or currency != "USD" or not BASE_SYMBOL.fullmatch(base):
            continue
        if code in KRAKEN_PAIRS or base in {"XBT", "BTC", "SOL", "BNB"}:
            continue
        if base in markets:
            ambiguous.add(base)
        markets[base] = (code, alias)
    for base in ambiguous:
        markets.pop(base, None)
    if not markets or len(markets) > 1000:
        raise ValueError("Unexpected Kraken catalog size")
    return {"status": "ready", "markets": markets}


_catalog = _throttled(read_catalog, success_ttl=900)
_watch_lock = Lock()
_watch_cache = OrderedDict()
_watch_fetches = deque()


def watch_catalog():
    catalog = _catalog()
    return {"status": catalog["status"], "bases": sorted(catalog.get("markets", {}))}


def watch_snapshot(base):
    if not BASE_SYMBOL.fullmatch(base):
        return {"status": "invalid"}
    catalog = _catalog()
    if catalog["status"] != "ready":
        return {"status": "unavailable"}
    pair = catalog["markets"].get(base)
    if pair is None:
        return {"status": "unsupported"}
    key = (base, *pair)
    # Bound request cost even when callers cycle through valid market IDs.
    if not _watch_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        now = time.monotonic()
        if key in _watch_cache:
            expires, result = _watch_cache[key]
            if now < expires:
                _watch_cache.move_to_end(key)
                return result
        while _watch_fetches and now - _watch_fetches[0] >= 60:
            _watch_fetches.popleft()
        if len(_watch_fetches) >= 10:
            return {"status": "rate-limited"}
        _watch_fetches.append(now)
        try:
            result = _read_kraken_market(*pair, f"kraken:{base}", _fetch, time.time)
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, RecursionError):
            result = {"status": "unavailable"}
        _watch_cache[key] = (time.monotonic() + 60, result)
        _watch_cache.move_to_end(key)
        if len(_watch_cache) > 32:
            _watch_cache.popitem(last=False)
        return result
    finally:
        _watch_lock.release()


STABLE_MARKET = "USDCUSD"
STABLE_OHLC = "https://api.kraken.com/0/public/OHLC?pair=USDCUSD&interval=60"


def read_stable_market(fetch=_fetch, clock=time.time):
    body = fetch(STABLE_OHLC)
    if body["error"] or set(body["result"]) != {STABLE_MARKET, "last"}:
        raise ValueError("Unexpected stablecoin market")
    rows = body["result"][STABLE_MARKET]
    if not isinstance(rows, list) or not 2 <= len(rows) <= 721:
        raise ValueError("No completed stablecoin candle")
    timestamp, _, _, _, close, *_ = rows[-2]
    now = clock()
    if type(timestamp) is not int or timestamp % HOUR or not now - 3 * HOUR < timestamp <= now - HOUR:
        raise ValueError("Stablecoin quote is stale or invalid")
    return {"status": "ready", "price": format(_price(close), "f"), "closedAt": _utc(timestamp + HOUR)}


stable_snapshot = _throttled(read_stable_market)
