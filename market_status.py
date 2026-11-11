"""Bounded public exchange status and Federal Reserve updates."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .public_chain import NoRedirect, fetch
from .zerion import label

CALENDAR = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
KRAKEN = "https://api.kraken.com/0/public/SystemStatus"
BINANCE = "https://api.binance.com/sapi/v1/system/status"
BYBIT = "https://api.bytick.com/v5/system/status"
MEETINGS = {
    2026: ((1, 27, 28), (3, 17, 18), (4, 28, 29), (6, 16, 17), (7, 28, 29), (9, 15, 16), (10, 27, 28), (12, 8, 9)),
    2027: ((1, 26, 27), (3, 16, 17), (4, 27, 28), (6, 8, 9), (7, 27, 28), (9, 14, 15), (10, 26, 27), (12, 7, 8)),
}
_cache = None


def upcoming(now):
    today = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    meetings = []
    for year, dates in MEETINGS.items():
        for month, first, last in dates:
            start = f"{year}-{month:02d}-{first:02d}"
            end = f"{year}-{month:02d}-{last:02d}"
            if end >= today:
                meetings.append({"start": start, "end": end})
    return meetings[:4]


def statements(raw):
    if len(raw) > 262144 or b"\0" in raw or b"<!DOCTYPE" in raw.upper():
        raise ValueError("RSS unavailable")
    root = ElementTree.fromstring(raw)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("RSS unavailable")
    found = {}
    for item in root.findall("channel/item")[:100]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title != "Federal Reserve issues FOMC statement":
            continue
        try:
            url = urlsplit(link)
            date = parsedate_to_datetime(item.findtext("pubDate", ""))
        except (TypeError, ValueError, OverflowError):
            continue
        if (url.scheme != "https" or url.netloc != "www.federalreserve.gov" or url.query or url.fragment
                or not re.fullmatch(r"/newsevents/pressreleases/monetary[0-9]{8}[a-z]\.htm", url.path)
                or date.tzinfo is None):
            continue
        found[link] = {"id": "fed-" + url.path.rsplit("/", 1)[1], "title": "FOMC statement released",
                       "detail": title, "asset": "Federal Reserve", "kind": "market-status", "severity": "info",
                       "at": date.astimezone(timezone.utc).isoformat(timespec="seconds"), "source": link}
    return sorted(found.values(), key=lambda row: row["at"], reverse=True)[:3]


def read_fed():
    req = Request(FED, headers={"Accept": "application/rss+xml", "User-Agent": "Holdings-Hermes/0.1"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=6) as response:
            return statements(response.read(262145))
    except (OSError, ValueError, ElementTree.ParseError, RecursionError):
        return None


def exchange_status(name, data):
    if name == "Kraken":
        result = data.get("result")
        state = result.get("status") if isinstance(result, dict) and data.get("error") == [] else None
        states = {"online": "Trading reported online", "maintenance": "Maintenance reported",
                  "cancel_only": "Cancel-only mode", "post_only": "Post-only mode", "limit_only": "Limit-only mode"}
        if isinstance(state, str) and state in states:
            return states[state], "info" if state == "online" else "warning"
    elif name == "Binance.com":
        code = data.get("status")
        if type(code) is int and code in (0, 1):
            return ("System reported normal", "info") if code == 0 else ("Maintenance reported", "warning")
    else:
        result = data.get("result")
        items = result.get("list") if isinstance(result, dict) and data.get("retCode") == 0 else None
        if isinstance(items, list) and len(items) <= 1000:
            live = [row for row in items if isinstance(row, dict) and row.get("env") == 1
                    and row.get("state") in ("scheduled", "ongoing")]
            if live:
                state = "ongoing" if any(row["state"] == "ongoing" for row in live) else "scheduled"
                title = label(next(row for row in live if row["state"] == state).get("title"), "Public notice")
                return f"{state.capitalize()} service notice · {title}", "warning" if state == "ongoing" else "info"
            return "No active notices in the public feed", "info"
    return "Status unavailable", "unavailable"


def read_exchange(name, url, at):
    try:
        data = fetch(url, timeout=6)
        detail, state = exchange_status(name, data if isinstance(data, dict) else {})
    except (OSError, ValueError, RecursionError):
        detail, state = "Status unavailable", "unavailable"
    return {"id": "exchange-" + name, "title": name, "asset": name, "kind": "market-status",
            "detail": detail, "severity": state, "at": at, "source": url}


def snapshot():
    global _cache
    clock = time.monotonic()
    if _cache and clock - _cache[0] < 120:
        return _cache[1]
    now = datetime.now(timezone.utc)
    at = now.isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=4) as workers:
        rss = workers.submit(read_fed)
        exchanges = [workers.submit(read_exchange, name, url, at) for name, url in
                     (("Kraken", KRAKEN), ("Binance.com", BINANCE), ("Bybit", BYBIT))]
        reports = [task.result() for task in exchanges]
        news = rss.result()
    recent = (now - timedelta(days=14)).isoformat(timespec="seconds")
    signals = [item for item in reports if item["severity"] == "warning"]
    signals.extend(item for item in (news or [])[:1] if recent <= item["at"] <= at)
    meetings = upcoming(now)
    soon = (now.astimezone(ZoneInfo("America/New_York")).date() + timedelta(days=2)).isoformat()
    if meetings and meetings[0]["start"] <= soon:
        first = meetings[0]
        signals.append({"id": "fomc-" + first["start"], "title": "FOMC meeting approaching",
                        "detail": first["start"] + " to " + first["end"] + " Eastern dates · Published schedule",
                        "asset": "Federal Reserve", "kind": "market-status", "severity": "info", "at": at, "source": CALENDAR})
    result = {"status": "ready", "at": at, "meetings": meetings, "calendar": CALENDAR,
              "exchanges": reports, "statements": news, "signals": signals}
    _cache = clock, result
    return result
