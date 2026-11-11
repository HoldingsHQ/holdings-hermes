"""Local, deterministic weekly briefing. Scheduled output contains no account details."""
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

from .alert_watch import MODE as WATCH_MODE
from .ledger import number
from .market import stable_snapshot
from .performance import MODE as PERFORMANCE_MODE, explain
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

MODE = "briefing-v1"
SAFE_SOURCES = {"Public wallets", "Kraken Spot", "Binance Spot", "Bybit Spot"}
LIMIT = 100


def stamp(now):
    return now.isoformat(timespec="seconds")


def within(value, start):
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return when.tzinfo is not None and when >= start
    except (AttributeError, ValueError):
        return False


def note_attention(kind, result, *, now=None, open_store=protected_snapshot):
    """Remember only review IDs and timestamps, with no ledger text or amounts."""
    try:
        saved, persist = open_store(MODE)
        state = saved or {"mode": MODE, "attention": {}}
        previous = state["attention"].get(kind, {"items": {}})
        items = dict(previous["items"])
        at = stamp(now or datetime.now(timezone.utc))
        if "items" in result:
            observed = result["items"]
            active = {row["id"] for row in observed if row["status"] in {"pending", "undone"}}
            items = {key: value for key, value in items.items() if key in active}
        else:
            observed = [result["item"]]
        for row in observed:
            key = row["id"]
            if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key):
                continue
            if row["status"] in {"pending", "undone"}:
                items.setdefault(key, at)
            else:
                items.pop(key, None)
        state["attention"][kind] = {"at": at, "items": dict(list(items.items())[-LIMIT:])}
        persist(state)
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        pass  # Monitoring cannot interfere with the underlying decision.


def risk_items(streams, attention, stable, *, now, threshold):
    lines = []

    def add(name, message, source):
        lines.append({"name": name, "text": message, "source": source})

    week = now - timedelta(days=7)
    recent = now - timedelta(hours=24)
    for source in sorted(SAFE_SOURCES):
        scopes = [points for points in streams.values() if points and points[-1].get("source") == source]
        if not scopes:
            continue
        points = max(scopes, key=lambda rows: rows[-1]["at"])
        latest = points[-1]
        evidence = source + " · balance refresh " + latest["at"]
        if not within(latest["at"], recent):
            add("Stale source", source + " has no recorded refresh in the last 24 hours.", evidence)
            continue
        earlier = [point for point in points[:-1] if within(point["at"], week)]
        change = explain(earlier[0], latest) if earlier else None
        if change:
            market = number(change["marketUsd"])
            units = number(change["unitChangeUsd"])
            trend = lambda value: "rose" if value > 0 else "fell" if value < 0 else "was unchanged"
            add("Since the earlier refresh", f"{source}: the priced market effect {trend(market)} and the balance-unit effect {trend(units)}.",
                evidence + " · comparison starts " + change["from"])
        if latest.get("permissions") == "broader":
            new = any(point.get("permissions") == "read-only" for point in earlier)
            add("Key permissions", source + (" newly reported broader key permissions." if new
                                             else " reported broader key permissions."), evidence)
        if latest["complete"] and len(latest["lines"]) == latest["observed"] and latest["lines"]:
            with localcontext() as context:
                context.prec = 100
                allocation = {}
                for row in latest["lines"]:
                    label = row.get("asset") or row["provider"].split(" ", 1)[-1]
                    allocation[label] = allocation.get(label, Decimal(0)) + number(row["units"]) * number(row["price"])
                total = sum(allocation.values())
                if total > 0:
                    asset, value = max(allocation.items(), key=lambda item: item[1])
                    if value * 100 > total * threshold:
                        add("Concentration", f"{asset} exceeded the {threshold}% threshold in this fully priced observed scope.", evidence)

    if stable.get("status") == "ready":
        if abs(number(stable["price"]) - 1) >= Decimal("0.01"):
            add("Stablecoin price check", "Kraken USDC/USD closed at least 1% from its dollar peg.",
                "Kraken USDC/USD · completed hour " + stable["closedAt"])
    else:
        add("Stablecoin coverage", "A fresh Kraken USDC/USD hourly close was unavailable.", "Kraken USDC/USD")
    for kind, review in sorted(attention.items()):
        count = sum(within(at, week) for at in review["items"].values())
        if count:
            add("Needs attention", f"{count} newly opened {kind} review item(s) in the latest saved selection.",
                f"Holdings {kind} review · {review['at']}")
    if not streams:
        add("Coverage", "Refresh a supported source with the protected vault open to start a live briefing.", "Holdings encrypted valuation history")
    else:
        add("Coverage", "Only the latest recorded scope per source is compared. Missing prices or incomplete flows can limit conclusions.", "Holdings encrypted valuation history")
    return lines


def _weekly_rows(streams, alerts, now):
    start = now - timedelta(days=7)

    def in_window(value):
        return within(value, start) and not within(value, now + timedelta(seconds=1))

    def item(name, text, source, available=True):
        return {"name": name, "text": text, "source": source, "available": available}

    changes, movers, positions = [], {}, []
    compared_positions = 0
    for source in sorted(SAFE_SOURCES):
        candidates = [points for points in streams.values() if points and points[-1].get("source") == source]
        if not candidates:
            continue
        # Compare one exact saved scope per source. Prefer the wider scope when the time is tied.
        points = max(candidates, key=lambda rows: (rows[-1]["at"], rows[-1]["observed"]))
        points = [point for point in points if in_window(point["at"])]
        if len(points) < 2:
            continue
        old, new = points[0], points[-1]
        evidence = f"{source} · saved refreshes {old['at']} to {new['at']}"
        change = explain(old, new)
        with localcontext() as context:
            context.prec = 100
            if change:
                price, units = number(change["marketUsd"]), number(change["unitChangeUsd"])
                changes.append(item("Value change", f"Priced balances changed by {price + units:+,.2f} USD. "
                                    f"Price effect {price:+,.2f} USD. Balance-unit effect {units:+,.2f} USD.", evidence))
            previous = {row["identity"]: row for row in old["lines"]}
            for row in new["lines"]:
                prior = previous.get(row["identity"])
                if (not prior or row["provider"] != prior["provider"] or not row.get("quoteAt") or not prior.get("quoteAt")
                        or row["quoteAt"] <= prior["quoteAt"] or min(number(prior["units"]), number(row["units"]),
                                                                  number(prior["price"])) <= 0):
                    continue
                percent = (number(row["price"]) / number(prior["price"]) - 1) * 100
                if percent:
                    key = (row["provider"], prior["quoteAt"], row["quoteAt"])
                    movers[key] = percent
        if old["complete"] and new["complete"] and old.get("positions") is not None and new.get("positions") is not None:
            compared_positions += 1
            seen = {identity for identity, _ in old["positions"]}
            added = [label for identity, label in new["positions"] if identity not in seen]
            if added:
                positions.append(item("New positions", f"{len(added)} newly observed holding(s): {', '.join(added[:5])}. "
                                      "A refresh does not identify how the holding was acquired.", evidence))

    ranked = sorted(movers.items(), key=lambda entry: (-abs(entry[1]), entry[0]))[:3]
    mover_rows = [item("Top movers", f"{provider} moved {move:+.2f}% between observed quotes.",
                       f"{provider} · hourly closes {before} to {after}") for (provider, before, after), move in ranked]
    watch = [entry for entry in alerts if in_window(entry["at"]) and not entry.get("fictional")]
    security = {"security", "hacks", "outflow", "lookalike"}

    def alert_summary(name, entries):
        if not entries:
            return item(name, "No matching saved watch findings in this window. Check coverage in Alerts.",
                        "Holdings saved watch history", False)
        latest = max(entries, key=lambda entry: entry["at"])
        return item(name, f"{len(entries)} saved finding(s). Open Alerts to review their evidence.",
                    f"Holdings saved watch history · latest {latest['at']} · {latest['id']}")

    return [
        *(changes or [item("Value change", "Comparable priced refreshes are unavailable in this window.",
                          "Holdings saved balance refreshes", False)]),
        *(mover_rows or [item("Top movers", "Comparable changed quotes for held assets are unavailable in this window.",
                            "Holdings saved quote timestamps", False)]),
        *(positions or [item("New positions", "No new holdings in the comparable saved observations." if compared_positions
                            else "Refresh the same supported scope twice to compare observed holdings.",
                            "Holdings saved balance refreshes", bool(compared_positions))]),
        alert_summary("Alerts", [entry for entry in watch if entry["rule"] not in security]),
        alert_summary("Security findings", [entry for entry in watch if entry["rule"] in security]),
    ]


def _sample_rows(now):
    old, new = stamp(now - timedelta(days=6)), stamp(now - timedelta(hours=1))
    def balance(at, price, positions):
        return {"source": "Public wallets", "at": at, "complete": True, "observed": 2, "positions": positions,
                "lines": [{"identity": "sample-weth", "units": "2", "price": price,
                           "quoteAt": at, "provider": "Fictional WETH/USD"}]}
    streams = {"fictional": [balance(old, "3000", [["sample-weth", "WETH"]]),
                              balance(new, "3120", [["sample-weth", "WETH"], ["sample-usdc", "USDC"]])]}
    alerts = [{"id": "sample:peg-warning", "at": new, "rule": "price"},
              {"id": "sample:wallet-security:token", "at": new, "rule": "security"}]
    return _weekly_rows(streams, alerts, now)


def scheduled_text(report):
    if report["status"] != "ready":
        return "[SILENT]"
    title = "Holdings weekly what changed" + (" (fictional)" if report["fictional"] else "")
    sections = sorted({row["name"] for row in report["items"] if row.get("available")})
    coverage = ", ".join(sections) + " available." if sections else "Limited saved observations."
    return f"{title}\n{coverage} Open Holdings → Portfolio → Weekly what changed for the cited details."


def preview(*, sample=False, threshold=Decimal(40), now=None, open_store=protected_snapshot, quote=stable_snapshot):
    now = now or datetime.now(timezone.utc)
    if sample:
        return {"status": "ready", "fictional": True, "at": stamp(now), "items": [
            *_sample_rows(now),
            {"name": "Needs attention", "text": "Review the possible transfer and its two original records.",
             "source": "Holdings 60-second fictional transfer"},
            {"name": "Concentration", "text": "ETH exceeds the illustrative 40% exposure threshold.",
             "source": "Holdings fictional Portfolio exposure"},
            {"name": "Coverage", "text": "This walkthrough uses fictional balances and decisions. No gateway delivery is active.",
             "source": "Holdings fictional demo coverage"},
        ]}
    try:
        history, _ = open_store(PERFORMANCE_MODE)
        monitored, _ = open_store(MODE)
        watch, _ = open_store(WATCH_MODE)
        streams = (history or {}).get("streams", {})
        rows = _weekly_rows(streams, (watch or {}).get("alerts", []), now)
        rows.extend(risk_items(streams, (monitored or {}).get("attention", {}), quote(), now=now, threshold=threshold))
        return {"status": "ready", "fictional": False, "at": stamp(now), "items": rows}
    except ProtectedKeyUnavailable as exc:
        return {"status": exc.status}
    except (ImportError, KeyError, IndexError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "storage-locked"}


def run(args):
    threshold = number(args.concentration)
    if not 1 <= threshold <= 100:
        print("Holdings briefing requires a concentration threshold from 1 to 100.")
        return 2
    report = preview(threshold=threshold)
    if args.scheduled:
        print(scheduled_text(report))
        return 0
    if report["status"] != "ready":
        print("Holdings briefing could not open this profile’s protected observations.")
        return 2
    print("Holdings weekly briefing" + (" (fictional)" if report["fictional"] else ""))
    for item in report["items"]:
        print(f"- {item['name']}: {item['text']} [{item['source']}]")
    return 0


def setup(parser):
    parser.add_argument("--scheduled", action="store_true", help="Return a redacted summary for Hermes delivery")
    parser.add_argument("--concentration", type=int, choices=range(1, 101), metavar="PERCENT", default=40)


def register(ctx):
    name = "holdings_weekly_briefing"

    def scheduled(args, **kwargs):
        if args != {} or ctx.current_cron_execution() is None:
            return json.dumps({"error": "This tool runs only in an opted-in Hermes scheduled job."})
        report = preview()
        return json.dumps({"output": scheduled_text(report), "status": report["status"]})

    ctx.register_cli_command("holdings-briefing", "Compute a local, evidence-linked Holdings briefing",
                             setup_fn=setup, handler_fn=run)
    ctx.register_tool(name=name, toolset="holdings_sample", schema={
        "name": name, "description": "Return the redacted weekly Holdings briefing for an opted-in Hermes Cron job.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }, handler=scheduled)
    ctx.register_automation_blueprint(
        "weekly-briefing", title="Holdings weekly what changed",
        description="Summarize changes in saved Holdings observations every Monday. Delivery contains section names only.",
        schedule_template="0 9 * * 1",
        prompt_template="Call holdings_weekly_briefing once with an empty object. Reply with exactly the returned output string.",
        deliver_default="local",
        slots=[{"name": "deliver", "type": "enum", "label": "Delivery target", "default": "local",
                "options": ["local", "origin"], "strict": False}],
        tags=["holdings", "briefing"],
    )
