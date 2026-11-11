"""Read-only Binance.com Spot balances and bounded fills."""
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .ledger import Entry, number
else:
    from ledger import Entry, number

PERMISSIONS_PATH = "/sapi/v1/account/apiRestrictions"
FILLS_PATH = "/api/v3/myTrades"
BALANCES_PATH = "/api/v3/account"
PAGE_SIZE = 1000
MAX_PAGES = 8
DAY_MS = 86_400_000
MAX_TIMESTAMP = 253_402_300_799_999
READ_FLAGS = {"enableReading", "enableFixReadOnly", "ipRestrict"}
PERMISSION_METADATA = {"createTime", "tradingAuthorityExpirationTime"}
REQUIRED_DENIALS = {"enableWithdrawals", "enableInternalTransfer", "enableMargin",
                    "enableFutures", "permitsUniversalTransfer", "enableSpotAndMarginTrading"}
ASSET = re.compile(r"[A-Z0-9]{1,32}")
ACCOUNT = re.compile(r"[A-Za-z0-9_-]{1,80}")
CAPABILITIES = {
    "exchange": "binance-global",
    "api_reads": (PERMISSIONS_PATH, FILLS_PATH, BALANCES_PATH),
    "csv_formats": (),
    "permissions": ("Enable Reading only",),
    "pagination": "Inclusive timestamp boundary, at most eight pages",
    "history_limits": "One explicit symbol and an inclusive window of up to 24 hours",
    "excluded": ("transfers", "derivatives", "regional Binance APIs"),
}


def _integer(value, maximum=2**63 - 1):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("Invalid Binance integer")
    return value


def _identifier(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Invalid Binance identifier")
    return value


def _signed_get(get, path, params, now_ms, api_key, secret):
    query = urlencode({**params, "recvWindow": 5000, "timestamp": _integer(now_ms(), MAX_TIMESTAMP)})
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return get(path, query + "&signature=" + signature, {"X-MBX-APIKEY": api_key})


def _verify_permissions(get, now_ms, api_key, secret):
    if _read_permissions(get, now_ms, api_key, secret) != "read-only":
        raise ValueError("Binance key permissions could not be verified as read-only")


def _read_permissions(get, now_ms, api_key, secret):
    try:
        permissions = _signed_get(get, PERMISSIONS_PATH, {}, now_ms, api_key, secret)
    except (OSError, TypeError, ValueError):
        return "unverified"
    if not isinstance(permissions, dict):
        return "unverified"
    if permissions.get("enableReading") is False:
        return "read-disabled"
    if any(value is True for name, value in permissions.items() if name not in READ_FLAGS | PERMISSION_METADATA):
        return "broader"
    if (permissions.get("enableReading") is True and all(permissions.get(flag) is False for flag in REQUIRED_DENIALS)
            and all(value is False for name, value in permissions.items() if name not in READ_FLAGS | PERMISSION_METADATA)):
        return "read-only"
    return "unverified"


def read_spot_balances(get, now_ms, api_key, secret):
    """Read free and locked Spot units. Return only fields used by Holdings."""
    permissions = _read_permissions(get, now_ms, api_key, secret)
    if permissions == "read-disabled":
        return {"status": "read-disabled"}
    try:
        account = _signed_get(get, BALANCES_PATH, {"omitZeroBalances": "true"}, now_ms, api_key, secret)
        if not isinstance(account, dict) or account.get("accountType") != "SPOT":
            raise ValueError("Unexpected account type")
        balances = account.get("balances")
        if not isinstance(balances, list) or len(balances) > 4096:
            raise ValueError("Unexpected balance list")
        rows, seen = [], set()
        for item in balances:
            asset = _identifier(item["asset"], ASSET)
            if asset in seen or not all(isinstance(item[name], str) for name in ("free", "locked")):
                raise ValueError("Invalid or duplicate Binance balance")
            seen.add(asset)
            free, locked = (number(item[name]) for name in ("free", "locked"))
            if min(free, locked) < 0:
                raise ValueError("Negative Binance balance")
            if free or locked:
                rows.append({"asset": asset, "free": format(free, "f"), "locked": format(locked, "f")})
        rows.sort(key=lambda row: row["asset"])
        fetched = datetime.fromtimestamp(_integer(now_ms(), MAX_TIMESTAMP) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (KeyError, OSError, OverflowError, TypeError, ValueError):
        return {"status": "unavailable", "permissionStatus": permissions}
    return {"status": "ready", "permissionStatus": permissions, "balances": rows, "fetchedAt": fetched}


def _normalize_fill(account, symbol, base, quote, row):
    if row["symbol"] != symbol or type(row["isBuyer"]) is not bool:
        raise ValueError("Binance fill does not match the requested market")
    fill_id = _integer(row["id"])
    order_id = _integer(row["orderId"])
    millis = _integer(row["time"], MAX_TIMESTAMP)
    at = (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=millis)).isoformat()
    quantity, cost, price, fee = (number(row[field]) for field in ("qty", "quoteQty", "price", "commission"))
    if quantity <= 0 or cost < 0 or price <= 0 or fee < 0:
        raise ValueError("Invalid Binance fill quantities")
    fee_asset = _identifier(row["commissionAsset"], ASSET)
    key = f"binance-global:{account}:spot:{symbol}:{fill_id}"
    return Entry(key, at, "buy" if row["isBuyer"] else "sell", f"binance-pair:{base}/{quote}",
                 quantity, (f"api:{key}",), f"binance-asset:{fee_asset}" if fee else "", fee,
                 related=f"binance-global:{account}:spot-order:{symbol}:{order_id}", cost=cost,
                 original=tuple(sorted((name, str(value)) for name, value in row.items())))


def read_spot_fills(get, account, symbol, base, quote, start, end, now_ms, api_key, secret):
    """Keep every request inside the selected time window, even when paging."""
    _identifier(account, ACCOUNT)
    for value in (symbol, base, quote):
        _identifier(value, ASSET)
    if symbol != base + quote or base == quote:
        raise ValueError("Binance market metadata does not match the symbol")
    _integer(start, MAX_TIMESTAMP)
    _integer(end, MAX_TIMESTAMP)
    if not 0 <= end - start < DAY_MS:
        raise ValueError("Binance Spot needs a window of up to 24 hours")
    _verify_permissions(get, now_ms, api_key, secret)

    observations = {}
    cursor = start
    pages = 0
    failed_pages = []
    complete = False
    gap = "page-limit"
    for page_number in range(1, MAX_PAGES + 1):
        try:
            page = _signed_get(get, FILLS_PATH, {"symbol": symbol, "startTime": cursor,
                               "endTime": end, "limit": PAGE_SIZE}, now_ms, api_key, secret)
            if not isinstance(page, list) or len(page) > PAGE_SIZE:
                raise ValueError("Invalid Binance fill page")
            batch = [_normalize_fill(account, symbol, base, quote, row) for row in page]
            times = [row["time"] for row in page]
            if (times != sorted(times) or any(not cursor <= value <= end for value in times)
                    or len({entry.key for entry in batch}) != len(batch)):
                raise ValueError("Inconsistent Binance fill page")
        except (KeyError, OSError, TypeError, ValueError):
            failed_pages.append(page_number)
            gap = "failed-page"
            break
        pages += 1
        observations.update(dict.fromkeys(batch))
        if len(page) < PAGE_SIZE:
            complete, gap = True, None
            break
        # Re-read the final millisecond. Advancing by one would silently skip
        # fills when the page ended partway through a timestamp.
        if times[-1] == cursor:
            gap = "saturated-millisecond"
            break
        cursor = times[-1]
    return list(observations), {"section": "spot-fills", "symbol": symbol, "start": start, "end": end,
                                "pages": pages, "failed_pages": failed_pages, "complete": complete,
                                "gap": gap}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_read_only(path, query, headers):
    if path not in {PERMISSIONS_PATH, FILLS_PATH, BALANCES_PATH}:
        raise ValueError("Unsupported Binance request")
    request = Request("https://api.binance.com" + path + "?" + query,
                      headers={**headers, "Accept": "application/json", "User-Agent": "Holdings-Hermes/0.1"},
                      method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=12) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError
        return json.loads(data)
    except (OSError, ValueError):
        raise OSError("Binance read did not complete") from None
