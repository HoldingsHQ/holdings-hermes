"""Bounded, read-only Zerion enrichment for selected public wallet/network pairs."""
import base64
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener

from .public_chain import MAX_BYTES, NoRedirect, pace

HOST = "api.zerion.io"
CHAINS = {1: "ethereum", 8453: "base", 42161: "arbitrum", 56: "binance-smart-chain", 4663: "robinhood"}
CHART_PERIODS = {"chart-day": "day", "chart-week": "week", "chart-month": "month", "chart-year": "year"}
SECTIONS = {"tokens", "defi", "nfts", "pnl", *CHART_PERIODS}
PNL_FIELDS = ("realized_gain", "unrealized_gain", "total_gain")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
PREVIEW = re.compile(r"https://token-icons\.s3\.amazonaws\.com/[A-Za-z0-9_./-]{1,250}\.(png|jpe?g|webp|gif)")
ALCHEMY_PREVIEW = re.compile(r"https://nft2-cdn\.alchemy\.com/[a-zA-Z0-9_-]{1,32}/image/"
                            r"[a-zA-Z0-9_=,-]{1,80}/[a-z0-9-]{1,32}/[a-zA-Z0-9_-]{1,100}")
PAGE_SIZE = 50
MAX_PAGES = 3
MAX_ROWS = PAGE_SIZE * MAX_PAGES


def get(path, params, key):
    auth = base64.b64encode((key + ":").encode()).decode()
    request = Request(f"https://{HOST}{path}?{urlencode(params)}", headers={
        "Accept": "application/json", "Authorization": "Basic " + auth,
        "User-Agent": "Holdings-Hermes/0.1",
    })
    pace(request.full_url)
    try:
        with build_opener(NoRedirect()).open(request, timeout=5) as response:
            if response.status == 202:
                return "indexing", None
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return "response-limit", None
        return "ready", json.loads(raw, parse_float=Decimal)
    except HTTPError as exc:
        return {401: "invalid-key", 403: "access-denied", 422: "unsupported", 429: "rate-limited"}.get(exc.code, "unavailable"), None
    except (OSError, ValueError):
        pass
    return "unavailable", None


def next_cursor(link, path):
    if not isinstance(link, str) or len(link) > 8192:
        raise ValueError("Invalid cursor")
    url = urlsplit(link)
    if url.scheme != "https" or url.netloc != HOST or url.path.rstrip("/") != path.rstrip("/") or url.fragment:
        raise ValueError("Invalid cursor")
    values = parse_qs(url.query, max_num_fields=24).get("page[after]", [])
    if len(values) != 1 or not values[0] or len(values[0]) > 4096 or not values[0].isascii():
        raise ValueError("Invalid cursor")
    return values[0]


def label(value, fallback):
    if not isinstance(value, str):
        return fallback
    # Strip line breaks and directionality controls from untrusted metadata.
    cleaned = "".join(char for char in value[:100] if char.isprintable()).strip()
    return cleaned or fallback


def money(value, *, signed=False):
    if type(value) is int:
        value = Decimal(value)
    if (isinstance(value, Decimal) and value.is_finite() and (signed or value >= 0)
            and -80 <= value.adjusted() <= 24 and len(value.as_tuple().digits) <= 100):
        return format(value, "f")
    return None


def contract_of(token, chain):
    implementations = [item["address"] for item in token["implementations"] if item["chain_id"] == chain]
    if len(implementations) != 1:
        raise ValueError("Unknown token identity")
    address = implementations[0]
    if address is not None and (not isinstance(address, str) or not ADDRESS.fullmatch(address)):
        raise ValueError("Unknown contract")
    return address.lower() if address else None


def chain_of(item):
    return item["relationships"]["chain"]["data"]["id"]


def fungible(item, chain, section):
    attrs = item["attributes"]
    kind = attrs["position_type"]
    if item["type"] != "positions" or (kind == "wallet") != (section == "tokens"):
        raise ValueError("Unexpected position type")
    token = attrs["fungible_info"]
    flags = attrs.get("flags") or {}
    token_flags = token.get("flags") or {}
    if flags.get("is_trash") is True or token_flags.get("is_trash") is True:
        return None
    contract = contract_of(token, chain)
    units, places = attrs["quantity"]["int"], attrs["quantity"]["decimals"]
    if (not isinstance(units, str) or not re.fullmatch(r"-?[0-9]{1,78}", units)
            or (units.startswith("-") and kind != "loan") or type(places) is not int or not 0 <= places <= 255):
        raise ValueError("Invalid token quantity")
    quantity = abs(Decimal(units)).scaleb(-places)
    if not quantity:
        return None
    receipt = None
    if attrs.get("receipt"):
        try:
            receipt = contract_of(attrs["receipt"]["fungible_info"], chain)
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    price = money(attrs.get("price")) if token_flags.get("verified") is True else None
    updated = attrs.get("updated_at")
    if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)", updated):
        updated = None
    category = {"deposit": "Supplied", "loan": "Borrowed", "locked": "Locked", "staked": "Staked",
                "reward": "Rewards", "investment": "Invested", "wallet": "Wallet token"}.get(kind, "Other position")
    module = attrs.get("protocol_module")
    if kind == "deposit" and isinstance(module, str) and module.lower() in {"liquidity", "liquidity_pool", "liquidity-pool", "amm"}:
        category = "Liquidity"
    return {"contract": contract, "receipt": receipt, "asset": label(token.get("symbol"), "Unlabelled token"),
            "units": format(quantity, "f"), "category": category, "protocol": label(attrs.get("protocol"), "Unknown protocol"),
            "displayable": flags.get("displayable") is not False, "price": price, "updatedAt": updated}


def nft(item, chain, included=None):
    attrs = item["attributes"]
    info = attrs["nft_info"]
    if item["type"] != "nft_positions":
        raise ValueError("Unexpected NFT type")
    if (info.get("flags") or {}).get("is_spam") is True:
        return None
    contract, token_id = info["contract_address"], info["token_id"]
    if not isinstance(contract, str) or not ADDRESS.fullmatch(contract) or not isinstance(token_id, str) or not re.fullmatch(r"[0-9]{1,78}", token_id):
        raise ValueError("Unknown NFT identity")
    preview = ((info.get("content") or {}).get("preview") or {}).get("url")
    floor = None
    relationship = item.get("relationships", {}).get("nft", {}).get("data") or {}
    related_id = relationship.get("id")
    related = (included or {}).get(related_id) if isinstance(related_id, str) else None
    if relationship.get("type") == "nfts" and related:
        # The live JSON:API resource is top-level and may omit its chain. Its
        # relationship ID ties it to the already chain-checked position.
        rel_chain = ((related.get("relationships") or {}).get("chain") or {}).get("data") or {}
        if not rel_chain or rel_chain.get("id") == chain:
            floor = nft_floor(related, contract, token_id)
    if floor is None:
        for entry in item.get("included") or []:
            if entry.get("type") == "nfts" and chain_of(entry) == chain:
                floor = nft_floor(entry, contract, token_id)
                if floor is not None:
                    break
    image = (preview if isinstance(preview, str)
             and (PREVIEW.fullmatch(preview) or ALCHEMY_PREVIEW.fullmatch(preview)) else None)
    return {"contract": contract.lower(), "tokenId": token_id, "asset": label(info.get("name"), "Collectible"),
            "collection": label((attrs.get("collection_info") or {}).get("name"), "Unknown collection"),
            "image": image, "floor": floor}


def nft_floor(entry, contract, token_id):
    detail = entry.get("attributes") or {}
    if str(detail.get("contract_address")).lower() != contract.lower() or detail.get("token_id") != token_id:
        return None
    floor = money(((detail.get("market_data") or {}).get("prices") or {}).get("floor"))
    return floor if floor is not None and Decimal(floor) > 0 else None


def included_nfts(data):
    result = {}
    items = data.get("included")
    if isinstance(items, list):
        for entry in items:
            if not isinstance(entry, dict) or entry.get("type") != "nfts" or not isinstance(entry.get("id"), str):
                continue
            identity = entry["id"]
            result[identity] = None if identity in result else entry
    return result


def portfolio_reference(base, chain, section, key, fetch):
    route = "/nft-portfolio/" if section == "nfts" else "/portfolio"
    status, data = fetch(base + route, {"currency": "usd"}, key)
    if status != "ready":
        return {"state": status, "value": None}
    try:
        record = data["data"]
        expected = "wallet_nft_portfolio" if section == "nfts" else "portfolio"
        if record["type"] == expected:
            distribution = record["attributes"]["positions_distribution_by_chain"]
            return {"state": "ready", "value": money(distribution.get(chain), signed=section == "tokens")}
    except (AttributeError, KeyError, TypeError):
        pass
    return {"state": "unavailable", "value": None}


def wallet_report(wallet, chain, section, key, fetch):
    base = f"/v1/wallets/{wallet}"
    path = base + ("/nft-positions/" if section == "nfts" else "/positions/")
    params = {"currency": "usd", "filter[chain_ids]": chain, "page[size]": PAGE_SIZE}
    if section == "nfts":
        params["include"] = "nfts"
    else:
        params["filter[positions]"] = "only_simple" if section == "tokens" else "only_complex"
        params["filter[trash]"] = "only_non_trash"
    rows, conflicted, cursors = {}, set(), set()
    skipped = pages = examined = 0
    state = "page-limit"
    for _ in range(MAX_PAGES):
        status, data = fetch(path, params.copy(), key)
        if status != "ready":
            state = status
            break
        if (not isinstance(data, dict) or not isinstance(data.get("data"), list)
                or not isinstance(data.get("links"), dict)):
            state = "unavailable"
            break
        pages += 1
        # The positions service can return more rows than requested in one response.
        # Keep the original total budget across all pages, including rejected rows.
        remaining = MAX_ROWS - examined
        included = included_nfts(data) if section == "nfts" else None
        for item in data["data"][:remaining]:
            examined += 1
            try:
                record = item["id"]
                if not isinstance(record, str) or not re.fullmatch(r"[ -~]{1,384}", record) or chain_of(item) != chain:
                    raise ValueError("Out-of-scope position")
                row = nft(item, chain, included) if section == "nfts" else fungible(item, chain, section)
            except (AttributeError, KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if row is None:
                continue
            identity = ((row["contract"], row["tokenId"]) if section == "nfts"
                        else row["contract"] if section == "tokens" else record)
            if identity in conflicted:
                continue
            row["id"] = record
            if identity in rows and rows[identity] != row:
                del rows[identity]
                conflicted.add(identity)
                skipped += 1
            else:
                rows[identity] = row
        link = data["links"].get("next")
        if len(data["data"]) > remaining or (examined == MAX_ROWS and link is not None):
            state = "row-limit"
            break
        if link is None:
            state = "incomplete" if skipped else "complete"
            break
        try:
            cursor = next_cursor(link, path)
            if cursor in cursors:
                raise ValueError("Repeated cursor")
        except ValueError:
            state = "incomplete"
            break
        cursors.add(cursor)
        params["page[after]"] = cursor
    reference = portfolio_reference(base, chain, section, key, fetch) if state == "complete" and section != "defi" else None
    return {"state": state, "pages": pages, "skipped": skipped, "rows": list(rows.values()), "reference": reference}


def wallet_pnl(wallet, chain, key, fetch):
    status, payload = fetch(f"/v1/wallets/{wallet}/pnl", {"currency": "usd", "filter[chain_ids]": chain}, key)
    if status != "ready":
        return {"state": status}
    try:
        record = payload["data"]
        if record["type"] not in {"pnl", "wallet_pnl", "wallet-pnl"} or record["id"].lower() != wallet:
            return {"state": "unavailable"}
        attrs = record["attributes"]
        values = {field: money(attrs.get(field), signed=True) for field in PNL_FIELDS}
        if all(value is None for value in values.values()):
            return {"state": "unavailable"}
        meta = payload.get("meta") or {}
        excluded = bool(meta.get("excluded_fungible_ids") or meta.get("excluded_fungible_implementations"))
        return {"state": "complete", "pnl": values, "exclusions": excluded}
    except (AttributeError, KeyError, TypeError, ValueError):
        return {"state": "unavailable"}


def wallet_chart(wallet, chain, period, key, fetch):
    status, payload = fetch(f"/v1/wallets/{wallet}/charts/{period}",
                            {"currency": "usd", "filter[chain_ids]": chain}, key)
    if status != "ready":
        return {"state": status}
    try:
        record = payload["data"]
        if record["type"] != "wallet_chart" or record["id"].lower() != f"{wallet}-{period}":
            return {"state": "unavailable"}
        raw = record["attributes"]["points"]
        if not isinstance(raw, list) or len(raw) > 512:
            return {"state": "unavailable"}
        if not raw:
            return {"state": "empty"}
        points = []
        previous = -1
        for stamp, amount in raw:
            value = money(amount, signed=True)
            if type(stamp) is not int or not previous < stamp <= 4_102_444_800 or value is None:
                return {"state": "unavailable"}
            points.append([stamp, value])
            previous = stamp
        return {"state": "complete", "points": points}
    except (AttributeError, KeyError, TypeError, ValueError):
        return {"state": "unavailable"}


def combined_chart(reports, selected_count):
    if selected_count < 2 or len(reports) != selected_count:
        return []
    if any(report["state"] != "complete" for report in reports):
        return []
    series = [dict(report["points"]) for report in reports]
    shared = series[0].keys() & series[1].keys()
    for points in series[2:]:
        shared &= points.keys()
    if len(shared) < 2:
        return []
    return [[stamp, format(sum(Decimal(points[stamp]) for points in series), "f")]
            for stamp in sorted(shared)]


def snapshot(selection, section, key, fetch=get):
    reports = []
    totals = {}
    with localcontext() as context:
        context.prec = 384
        for wallet, chain_id in selection:
            chain = CHAINS[chain_id]
            if section == "pnl":
                report = wallet_pnl(wallet, chain, key, fetch)
            elif section in CHART_PERIODS:
                report = wallet_chart(wallet, chain, CHART_PERIODS[section], key, fetch)
            else:
                report = wallet_report(wallet, chain, section, key, fetch)
            reports.append(report)
            reference = report.get("reference")
            if report["state"] in {"rate-limited", "invalid-key", "access-denied"} or (reference and reference["state"] == "rate-limited"):
                break
        if (section == "pnl" and reports and len(reports) == len(selection)
                and all(report["state"] == "complete" for report in reports)):
            for field in PNL_FIELDS:
                values = [report["pnl"][field] for report in reports]
                if all(value is not None for value in values):
                    totals[field] = format(sum(Decimal(value) for value in values), "f")
        chart = combined_chart(reports, len(selection)) if section in CHART_PERIODS else None
    return {"status": "ready", "wallets": reports,
            **({"totals": totals} if section == "pnl" else {}),
            **({"combinedPoints": chart} if chart is not None else {}),
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
