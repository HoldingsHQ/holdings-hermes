"""Keyless USD prices for exchange Spot balances from each exchange's own public ticker.

One bounded, no-redirect GET per exchange read. A quote counts only when its last price and
24 hour volume are positive and, where the exchange reports a time, it is from the last hour.
USD stablecoins and Kraken's USD cash balance count at $1 and say so."""
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from urllib.parse import quote as urlquote

from . import public_chain

URLS = {
    # Binance rejects the whole batch if one symbol is unknown, so the cheap price list picks the
    # symbols first and the 24 hour ticker then supplies volume and close time for those only.
    "binance": "https://api.binance.com/api/v3/ticker/price",
    "kraken": "https://api.kraken.com/0/public/Ticker",
    "bybit": "https://api.bytick.com/v5/market/tickers?category=spot",
}
NAMES = {"binance": "Binance", "kraken": "Kraken", "bybit": "Bybit"}
PAR = {"USDC": "USDC counted at $1", "USDT": "USDT counted at $1", "ZUSD": "US dollar cash", "USD": "US dollar cash"}
MAX_AGE_MS = 3_600_000
MAX_SKEW_MS = 300_000  # clocks can differ a little, and the reply arrives after the request starts
MAX_PRICE = Decimal("1e9")
BINANCE_DAY = "https://api.binance.com/api/v3/ticker/24hr?type=MINI&symbols="
MAX_SYMBOLS = 100
TIMEOUT = 10


def positive(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and Decimal(0) < number < MAX_PRICE else None


def volume(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return False
    return number.is_finite() and number > 0


def stamp(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def binance_book(data, now_ms):
    book = {}
    if not isinstance(data, list):
        return book
    for item in data[:5000]:
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
            continue
        price, closed = positive(item.get("lastPrice")), item.get("closeTime")
        if price is None or not volume(item.get("volume")) or type(closed) is not int or not -MAX_SKEW_MS <= now_ms - closed <= MAX_AGE_MS:
            continue
        book[item["symbol"]] = (price, stamp(closed))
    return book


def bybit_book(data, now_ms):
    book = {}
    if not isinstance(data, dict) or data.get("retCode") != 0:
        return book
    reported = data.get("time")
    if type(reported) is not int or not -MAX_SKEW_MS <= now_ms - reported <= MAX_AGE_MS:
        return book
    result = data.get("result")
    items = result.get("list") if isinstance(result, dict) else None
    for item in items[:5000] if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str):
            continue
        price = positive(item.get("lastPrice"))
        if price is not None and volume(item.get("volume24h")):
            book[item["symbol"]] = (price, stamp(reported))
    return book


def kraken_book(data, now_ms):
    book = {}
    if not isinstance(data, dict) or data.get("error") != [] or not isinstance(data.get("result"), dict):
        return book
    at = stamp(now_ms)
    for pair, item in list(data["result"].items())[:5000]:
        try:
            price, traded, trades = positive(item["c"][0]), item["v"][1], item["t"][1]
        except (KeyError, IndexError, TypeError):
            continue
        if price is not None and volume(traded) and type(trades) is int and trades > 0:
            book[pair] = (price, at)
    return book


def candidates(exchange, asset):
    if exchange == "kraken":
        return [asset + "ZUSD", asset + "USD"]
    return [asset + "USDC", asset + "USDT"]


KRAKEN_SYMBOLS = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC", "XXLM": "XLM", "XXMR": "XMR",
                  "XZEC": "ZEC", "XETC": "ETC", "XXDG": "DOGE", "XDG": "DOGE", "ZEUR": "EUR", "ZGBP": "GBP", "ZCAD": "CAD"}


def quote_name(exchange, pair, asset):
    quote = pair[len(asset):]
    base = KRAKEN_SYMBOLS.get(asset, asset) if exchange == "kraken" else asset
    return f"{NAMES[exchange]} {base}/{'USD' if quote in {'ZUSD', 'USD'} else quote} price"


def quotes(exchange, assets, get=None, now_ms=None):
    """Return {asset: {"price", "at", "provider"}} for the assets that have a usable public quote."""
    if exchange not in URLS:
        raise ValueError("Unsupported exchange")
    moment = int(time.time() * 1000) if now_ms is None else now_ms
    wanted = sorted({asset for asset in assets if isinstance(asset, str) and 0 < len(asset) <= 24})
    prices = {asset: {"price": "1", "at": stamp(moment), "provider": PAR[asset]} for asset in wanted if asset in PAR}
    rest = [asset for asset in wanted if asset not in prices]
    if not rest:
        return prices
    fetch = get or public_chain.fetch
    try:
        data = fetch(URLS[exchange], timeout=TIMEOUT)
        if exchange == "binance":
            listed = {item.get("symbol") for item in data[:5000] if isinstance(item, dict)} if isinstance(data, list) else set()
            symbols = [pair for asset in rest for pair in candidates(exchange, asset) if pair in listed][:MAX_SYMBOLS]
            if not symbols:
                return prices
            data = fetch(BINANCE_DAY + urlquote(json.dumps(symbols, separators=(",", ":"))), timeout=TIMEOUT)
    except (OSError, ValueError, TypeError):
        return prices
    book = {"binance": binance_book, "bybit": bybit_book, "kraken": kraken_book}[exchange](data, moment)
    for asset in rest:
        for pair in candidates(exchange, asset):
            if pair in book:
                price, at = book[pair]
                prices[asset] = {"price": format(price, "f"), "at": at, "provider": quote_name(exchange, pair, asset)}
                break
    return prices
