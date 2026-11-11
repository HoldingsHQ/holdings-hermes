"""Read-only Bybit Global Unified Account Spot balances and fills."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import localcontext
from urllib.parse import unquote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .ledger import Entry, number
else:
    from ledger import Entry, number

PERMISSIONS_PATH = "/v5/user/query-api"
FILLS_PATH = "/v5/execution/list"
BALANCES_PATH = "/v5/account/wallet-balance"
MAX_WINDOW_MS = 7 * 86_400_000
MAX_TIMESTAMP = 253_402_300_799_999
PAGE_SIZE = 100
MAX_PAGES = 5
TOKEN = re.compile(r"[A-Z0-9]{1,32}")
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,80}")
CURSOR = re.compile(r"[A-Za-z0-9_,:./=+~-]{1,512}")
CAPABILITIES = {
    "exchange": "bybit-global",
    "api_reads": (PERMISSIONS_PATH, FILLS_PATH, BALANCES_PATH),
    "csv_formats": (),
    "permissions": ("Read-Only", "Unified Trading Spot only"),
    "pagination": "Bybit cursor; at most five pages of 100 executions",
    "history_limits": "All Spot markets or one symbol in an explicit UTC window of up to seven days",
    "excluded": ("legacy Spot Account archive", "transfers", "derivatives", "regional hosts"),
}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_time(value):
    return type(value) is int and 0 <= value <= MAX_TIMESTAMP


def _result(get, path, params, now_ms, key, secret):
    query = urlencode(sorted(params.items()))
    timestamp = now_ms()
    if not _valid_time(timestamp):
        raise ValueError("Invalid Bybit signing time")
    window = "5000"
    payload = f"{timestamp}{key}{window}{query}".encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    reply = get(path, query, {
        "X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": window, "X-BAPI-SIGN": signature,
    })
    if (not isinstance(reply, dict) or type(reply.get("retCode")) is not int
            or reply["retCode"] != 0 or not isinstance(reply.get("result"), dict)):
        raise ValueError("Bybit read failed")
    return reply["result"]


def _require_spot_only(get, now_ms, key, secret):
    try:
        result = _result(get, PERMISSIONS_PATH, {}, now_ms, key, secret)
        permissions = result.get("permissions")
        if (type(result.get("readOnly")) is not int or result["readOnly"] != 1
                or type(result.get("uta")) is not int or result["uta"] != 1
                or not isinstance(permissions, dict) or permissions.get("Spot") != ["SpotTrade"]
                or any(value != [] for name, value in permissions.items() if name != "Spot")):
            raise ValueError
    except (OSError, TypeError, ValueError):
        raise ValueError("Bybit key could not be verified as read-only Unified Spot only") from None


def read_spot_balances(get, now_ms, key, secret):
    """Show Unified wallet units net of Spot borrow and Spot order locks."""
    permission_status = "unverified"
    try:
        key_info = _result(get, PERMISSIONS_PATH, {}, now_ms, key, secret)
        if type(key_info.get("uta")) is int and key_info["uta"] == 0:
            return {"status": "unsupported-account"}
        scopes = key_info.get("permissions")
        read_only = key_info.get("readOnly")
        if type(read_only) is int and read_only == 0:
            permission_status = "broader"
        elif (type(read_only) is int and read_only == 1 and isinstance(scopes, dict)
                and all(isinstance(flags, list) and all(isinstance(flag, str) for flag in flags)
                        for flags in scopes.values())):
            extra = any(set(flags) - ({"SpotTrade"} if scope == "Spot" else set()) for scope, flags in scopes.items())
            permission_status = "broader" if extra else "read-only"
    except (OSError, TypeError, ValueError):
        pass

    try:
        accounts = _result(get, BALANCES_PATH, {"accountType": "UNIFIED"}, now_ms, key, secret)["list"]
        if not isinstance(accounts, list) or len(accounts) != 1 or not isinstance(accounts[0], dict):
            raise ValueError("Invalid Bybit wallet account")
        coins = accounts[0]["coin"]
        if accounts[0].get("accountType") != "UNIFIED" or not isinstance(coins, list) or len(coins) > 4096:
            raise ValueError("Invalid Bybit Unified wallet")
        rows, seen = [], set()
        with localcontext() as context:
            context.prec = 250
            for coin in coins:
                if (not isinstance(coin, dict) or not _matches(TOKEN, coin.get("coin")) or coin["coin"] in seen
                        or not all(isinstance(coin.get(name), str) for name in ("walletBalance", "spotBorrow", "locked"))):
                    raise ValueError("Invalid Bybit wallet row")
                asset = coin["coin"]
                seen.add(asset)
                balance, borrowed, locked = (number(coin[name]) for name in ("walletBalance", "spotBorrow", "locked"))
                if min(borrowed, locked) < 0:
                    raise ValueError("Invalid Bybit borrow or lock")
                if balance or borrowed or locked:
                    free = balance - borrowed - locked
                    rows.append({"asset": asset, "free": None if free < 0 else format(free, "f"),
                                 "locked": format(locked, "f")})
        rows.sort(key=lambda row: row["asset"])
    except (KeyError, OSError, TypeError, ValueError):
        return {"status": "unavailable", "permissionStatus": permission_status}
    return {"status": "ready", "permissionStatus": permission_status, "balances": rows,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _execution(account, symbol, base, quote, row, start, end):
    if (not isinstance(row, dict) or not _matches(TOKEN, row.get("symbol"))
            or (symbol is not None and row["symbol"] != symbol) or row.get("execType") != "Trade"
            or row.get("side") not in ("Buy", "Sell")
            or not all(_matches(IDENTIFIER, row.get(field)) for field in ("execId", "orderId"))):
        raise ValueError("Bybit execution does not match the requested Spot market")
    raw_time = row["execTime"]
    if not isinstance(raw_time, str) or not re.fullmatch(r"[0-9]{1,15}", raw_time):
        raise ValueError("Invalid Bybit execution time")
    millis = int(raw_time)
    if not start <= millis <= end:
        raise ValueError("Bybit execution outside the requested window")
    at = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=millis)).isoformat()
    quantity, cost, price, fee = (number(row[field]) for field in ("execQty", "execValue", "execPrice", "execFee"))
    if quantity <= 0 or cost < 0 or price <= 0:
        raise ValueError("Invalid Bybit fill quantities")
    asset = row.get("feeCurrency")
    fee_unknown = bool(fee) and not _matches(TOKEN, asset)
    market = row["symbol"]
    pair = f"{base}/{quote}" if symbol is not None else market
    identity = f"bybit-global:{account}:spot:{market}:{row['execId']}"
    order = f"bybit-global:{account}:spot-order:{market}:{row['orderId']}"
    extra_fees = row.get("extraFees") not in (None, "", [])
    entry = Entry(identity, at, row["side"].lower(), f"bybit-pair:{pair}", quantity,
                  (f"api:{identity}",), f"bybit-asset:{asset}" if fee and not fee_unknown else "",
                  fee, related=order, cost=cost,
                  original=tuple(sorted((name, str(value)) for name, value in row.items())))
    return entry, fee_unknown or extra_fees


def read_spot_fills(get, account, symbol, base, quote, start, end, now_ms, api_key, secret):
    if (not all(_matches(TOKEN, value) for value in (symbol, base, quote))
            or base == quote or symbol != base + quote):
        raise ValueError("Invalid Bybit Spot market scope")
    return _read(get, account, symbol, base, quote, start, end, now_ms, api_key, secret)


def read_spot_activity(get, account, start, end, now_ms, api_key, secret):
    return _read(get, account, None, None, None, start, end, now_ms, api_key, secret)


def _read(get, account, symbol, base, quote, start, end, now_ms, api_key, secret):
    if not _matches(IDENTIFIER, account):
        raise ValueError("Invalid Bybit account scope")
    if not _valid_time(start) or not _valid_time(end) or not 0 <= end - start < MAX_WINDOW_MS:
        raise ValueError("Bybit Spot needs an explicit window of up to seven days")
    _require_spot_only(get, now_ms, api_key, secret)

    params = {"category": "spot", "execType": "Trade",
              "startTime": start, "endTime": end, "limit": PAGE_SIZE}
    if symbol is not None:
        params["symbol"] = symbol
    entries, unresolved_fees, seen_cursors = {}, set(), set()
    pages = 0
    failed_pages = []
    gap = "page-limit"
    for page_number in range(1, MAX_PAGES + 1):
        try:
            result = _result(get, FILLS_PATH, params, now_ms, api_key, secret)
            batch, raw_cursor = result["list"], result["nextPageCursor"]
            if result.get("category") != "spot" or not isinstance(batch, list) or len(batch) > PAGE_SIZE:
                raise ValueError("Invalid Bybit Spot page")
            page = [_execution(account, symbol, base, quote, row, start, end) for row in batch]
            if not isinstance(raw_cursor, str) or len(raw_cursor) > 1536:
                raise ValueError("Invalid Bybit cursor")
            cursor = unquote(raw_cursor)
            if cursor and not _matches(CURSOR, cursor):
                raise ValueError("Invalid Bybit cursor")
        except (KeyError, OSError, TypeError, ValueError):
            failed_pages.append(page_number)
            gap = "failed-page"
            break
        pages += 1
        for entry, uncertain_fee in page:
            entries.setdefault(entry, None)
            if uncertain_fee:
                unresolved_fees.add(entry.key)
        if not cursor:
            gap = None
            break
        if cursor in seen_cursors:
            gap = "repeated-cursor"
            break
        seen_cursors.add(cursor)
        params["cursor"] = cursor
    return list(entries), {"section": "spot-fills", "symbol": symbol, "start": start, "end": end,
                           "pages": pages, "failed_pages": failed_pages, "complete": gap is None,
                           "gap": gap, "unresolved_fees": sorted(unresolved_fees)}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_read_only(path, query, headers):
    if path not in {PERMISSIONS_PATH, FILLS_PATH}:
        raise ValueError("Unsupported Bybit request")
    return _get("https://api.bybit.com", path, query, headers)


def get_balance_read_only(path, query, headers):
    if path not in {PERMISSIONS_PATH, BALANCES_PATH}:
        raise ValueError("Unsupported Bybit balance request")
    return _get("https://api.bytick.com", path, query, headers)


def _get(host, path, query, headers):
    url = host + path + ("?" + query if query else "")
    request = Request(url, headers={**headers, "Accept": "application/json",
                                   "User-Agent": "Holdings-Hermes/0.1"}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=12) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError
        return json.loads(data)
    except (OSError, ValueError):
        raise OSError("Bybit read did not complete") from None
