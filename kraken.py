"""Read-only Kraken Spot balances and history."""
import base64
import csv
import hashlib
import hmac
import io
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .ledger import Entry, Ledger, number
else:
    from ledger import Entry, Ledger, number

HISTORY = {"TradesHistory": "trades", "Ledgers": "ledger"}
REQUIRED = {
    "trades": {"txid", "time", "pair", "type", "vol", "cost", "fee"},
    "ledger": {"txid", "refid", "time", "type", "asset", "amount", "fee"},
}
MAX_HISTORY_PAGES = 20
KEY_INFO_PATH = "/0/private/GetApiKeyInfo"
BALANCE_PATH = "/0/private/BalanceEx"
QUERY_ONLY = frozenset(("query-closed-trades", "query-ledger"))
BALANCE_READ_PERMISSIONS = QUERY_ONLY | {"query-funds", "query-open-trades"}
CAPABILITIES = {
    "exchange": "kraken",
    "api_reads": tuple(HISTORY),
    "csv_formats": ("Kraken Trades", "Kraken Ledgers"),
    "permissions": ("Query Closed Orders & Trades", "Query Ledger Entries"),
    "pagination": "offset; deduplicate by transaction ID within each stream",
    "history_limits": "Explicit UTC start/end. Default 3 pages per stream; maximum 20.",
}
ASSET = re.compile(r"^[A-Za-z0-9.]{1,32}$")
PAIR = re.compile(r"^[A-Za-z0-9./]{1,32}$")
PAIR_ALIASES = {"XBT/USD": "BTC/USD", "XBTUSD": "BTC/USD",
                "XXBTZUSD": "BTC/USD", "BTCUSD": "BTC/USD"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
KINDS = {"deposit": "deposit", "withdrawal": "withdrawal", "transfer": "transfer",
         "trade": "trade_movement", "staking": "reward", "dividend": "reward",
         "rollover": "fee_movement"}


def checked(value, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError("Invalid Kraken identifier")
    return value


def timestamp(value, channel):
    if channel == "csv":
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S" if "." not in value
                                 else "%Y-%m-%d %H:%M:%S.%f").replace(
                                     tzinfo=timezone.utc).isoformat()
    decimal = number(value)
    if not 0 <= decimal <= 253402300799 or decimal.as_tuple().exponent < -6:
        raise ValueError("Kraken timestamp out of bounds")
    micros = decimal * 1_000_000
    if micros != micros.to_integral_value():
        raise ValueError("Unsupported sub-microsecond Kraken timestamp")
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) +
            timedelta(microseconds=int(micros))).isoformat()


def normalize(account, section, txid, row, channel):
    account = checked(account, IDENTIFIER)
    txid = checked(txid, IDENTIFIER)
    key = f"kraken:{account}:{section}:{txid}"
    evidence = (f"{channel}:{key}",)
    at = timestamp(row["time"], channel)
    original = tuple(sorted((key, str(value)) for key, value in row.items()))
    if section == "trades":
        pair = checked(row["pair"], PAIR)
        pair = PAIR_ALIASES.get(pair, pair)
        side = row["type"]
        if side not in {"buy", "sell"}:
            raise ValueError("Unknown Kraken trade side")
        # TradesHistory's quote-currency fee is not authoritative. Ledger fees
        # are emitted separately from the asset actually debited.
        return Entry(key, at, side, f"kraken-pair:{pair}", number(row["vol"]), evidence,
                     cost=number(row["cost"]), original=original)
    asset = "kraken-asset:" + checked(row["asset"], ASSET)
    raw_kind = row["type"]
    related = checked(row["refid"], IDENTIFIER)
    kind = KINDS.get(raw_kind, "unresolved")
    if raw_kind == "earn" and row.get("subtype") == "reward":
        kind = "reward"
    fee = number(row["fee"])
    raw_fee_asset = row.get("feecurrency") or row["asset"]
    fee_asset = "kraken-asset:" + checked(raw_fee_asset, ASSET) if fee else ""
    return Entry(key, at, kind, asset, number(row["amount"]), evidence,
                 fee_asset, fee, related, original=original)


def read_export(payload, section, account):
    if section not in REQUIRED or len(payload) > 2_000_000:
        raise ValueError("Unsupported Kraken export")
    try:
        rows = csv.DictReader(io.StringIO(payload.removeprefix("\ufeff"), newline=""), strict=True)
        header = rows.fieldnames or []
        if not REQUIRED[section].issubset(header) or len(header) != len(set(header)):
            raise ValueError("Unexpected Kraken export columns")
        entries = []
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Invalid Kraken export row")
            if section == "trades":
                number(row["fee"])
            entries.append(normalize(account, section, row["txid"], row, "csv"))
        return entries
    except csv.Error:
        raise ValueError("Invalid Kraken export row") from None


def sign(path, nonce, body, secret):
    digest = hashlib.sha256(str(nonce).encode() + body).digest()
    mac = hmac.new(base64.b64decode(secret, validate=True), path.encode() + digest,
                   hashlib.sha512).digest()
    return base64.b64encode(mac).decode()


def _signed_read(post, path, next_nonce, api_key, secret):
    nonce = next_nonce()
    body = urlencode({"nonce": nonce}).encode()
    headers = {"API-Key": api_key, "API-Sign": sign(path, nonce, body, secret)}
    reply = post(path, body, headers)
    if not isinstance(reply, dict) or reply.get("error") != [] or not isinstance(reply.get("result"), dict):
        raise ValueError("Invalid Kraken read response")
    return reply["result"]


def verify_query_permissions(post, next_nonce, api_key, secret):
    """Require the two Spot history permissions and allow optional Query Funds."""
    try:
        result = _signed_read(post, KEY_INFO_PATH, next_nonce, api_key, secret)
        permissions = result["permissions"]
        if not isinstance(permissions, list) or not all(isinstance(flag, str) for flag in permissions):
            raise ValueError
        if not QUERY_ONLY <= set(permissions) <= QUERY_ONLY | {"query-funds"}:
            raise ValueError
        lower, upper = int(result.get("queryFrom", 0)), int(result.get("queryTo", 0))
        if lower < 0 or upper < 0 or (upper and lower > upper):
            raise ValueError
    except (KeyError, OSError, TypeError, ValueError):
        raise ValueError("Kraken key permissions could not be verified as query-only") from None
    return {"query_from": lower or None, "query_to": upper or None}


def read_spot_balances(post, next_nonce, api_key, secret):
    """Keep Kraken asset IDs and non-margin Spot order holds in native units."""
    permission_status = "unverified"
    try:
        permissions = _signed_read(post, KEY_INFO_PATH, next_nonce, api_key, secret).get("permissions")
        if isinstance(permissions, list) and all(isinstance(flag, str) for flag in permissions):
            if "query-funds" not in permissions:
                return {"status": "read-disabled"}
            permission_status = "broader" if set(permissions) - BALANCE_READ_PERMISSIONS else "read-only"
    except (OSError, TypeError, ValueError):
        pass

    try:
        balances = _signed_read(post, BALANCE_PATH, next_nonce, api_key, secret)
        if len(balances) > 4096:
            raise ValueError("Unexpected Kraken balance list")
        rows = []
        with localcontext() as context:
            context.prec = 250
            for asset, fields in sorted(balances.items()):
                checked(asset, ASSET)
                if not isinstance(fields, dict):
                    raise ValueError("Invalid Kraken balance")
                balance = number(fields["balance"])
                locked = number(fields["hold_trade"])
                credit, used = (number(fields.get(name, "0")) for name in ("credit", "credit_used"))
                if min(locked, credit, used) < 0:
                    raise ValueError("Invalid Kraken hold or credit")
                if balance or locked or credit or used:
                    free = None if credit or used or balance < locked else format(balance - locked, "f")
                    rows.append({"asset": asset, "free": free, "locked": format(locked, "f")})
    except (KeyError, OSError, TypeError, ValueError):
        return {"status": "unavailable", "permissionStatus": permission_status}
    return {"status": "ready", "permissionStatus": permission_status, "balances": rows,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def reconcile(api_entries, csv_entries):
    """Merge by normalized source identity, and retain every observation."""
    ledger = Ledger()
    candidates = {}
    for row in api_entries:
        ledger.add(row)
        candidates.setdefault(row.key, set()).add(row.facts())
    for row in csv_entries:
        facts = candidates.get(row.key, ())
        if len(facts) == 1:
            api_at, *api_values = next(iter(facts))
            csv_at, *csv_values = row.facts()
            # Official CSV can truncate fractional time. Only align a single,
            # otherwise-identical API observation in that exact UTC second.
            if (csv_at[19:] == "+00:00" and "." not in dict(row.original)["time"] and
                api_at[:19] == csv_at[:19] and api_values == csv_values):
                row = replace(row, at=api_at)
        ledger.add(row)
    return ledger


def read_history(post, method, account, start, end, next_nonce, api_key, secret,
                 max_pages=3):
    """Fetch a bounded UTC interval. Post must keep HTTPS, honor timeouts and redact errors."""
    if method not in HISTORY or not 0 <= start <= end or not 1 <= max_pages <= MAX_HISTORY_PAGES:
        raise ValueError("Invalid Kraken history request")
    section = HISTORY[method]
    path = "/0/private/" + method
    entries = []
    seen = set()
    counts = set()
    offset = 0
    pages = 0
    failed_pages = []
    for page_number in range(1, max_pages + 1):
        nonce = next_nonce()
        params = {"nonce": nonce, "start": start, "end": end, "ofs": offset}
        if section == "trades":
            params["consolidate_taker"] = "false"
        body = urlencode(params).encode()
        headers = {"API-Key": api_key, "API-Sign": sign(path, nonce, body, secret)}
        try:
            response = post(path, body, headers)
            if response["error"]:
                raise ValueError("Kraken rejected the history request")
            result = response["result"]
            page, count = result[section], int(result["count"])
            if not isinstance(page, dict) or len(page) > 50 or count < offset + len(page):
                raise ValueError("Kraken returned an inconsistent page")
            batch = [normalize(account, section, txid, row, "api") for txid, row in page.items()]
            if any(not start <= datetime.fromisoformat(row.at).timestamp() <= end for row in batch):
                raise ValueError("Kraken returned an out-of-range row")
        except (KeyError, OSError, TypeError, ValueError):
            failed_pages.append(page_number)
            break
        pages += 1
        counts.add(count)
        entries.extend(batch)
        seen.update(page)
        offset += len(page)
        if not page or offset >= count:
            break
    coverage = {"section": section, "start": start, "end": end, "seen": len(seen),
                "pages": pages, "failed_pages": failed_pages,
                "complete": not failed_pages and len(counts) == 1 and len(seen) == offset == next(iter(counts))}
    return entries, coverage


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_read_only(path, body, headers):
    """Send only Kraken Spot query requests. Never expose the response on error."""
    if path not in {KEY_INFO_PATH, BALANCE_PATH, "/0/private/TradesHistory", "/0/private/Ledgers"}:
        raise ValueError("Unsupported Kraken request")
    request = Request("https://api.kraken.com" + path, data=body, headers={
        **headers, "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "User-Agent": "Holdings-Hermes/0.1",
    })
    try:
        with build_opener(_NoRedirect()).open(request, timeout=8) as response:
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("Kraken reply exceeds limit")
        return json.loads(data, parse_float=Decimal)
    except (OSError, ValueError):
        raise OSError("Kraken read did not complete") from None
