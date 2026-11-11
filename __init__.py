"""Holdings Hermes registration."""
import json
import re
from datetime import date
from pathlib import Path

SAMPLE = Path(__file__).with_name("dashboard") / "sample.json"


SAMPLE_TOOL = {
    "name": "holdings_sample_lookup",
    "description": "Read fees, swap legs, or coverage from the fictional Holdings records. Cite returned evidence IDs. No real accounts or actions.",
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "enum": ["fees", "swaps", "coverage"]},
            "source": {"type": "string", "enum": ["all", "kraken", "wallet"]},
            "start": {"type": "string", "description": "First UTC date, YYYY-MM-DD."},
            "end": {"type": "string", "description": "Last UTC date, YYYY-MM-DD."},
        },
        "required": ["topic"],
        "additionalProperties": False,
    },
}


def sample_data():
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    rows = []
    for number, row in enumerate(data["rows"], 1):
        rows.append({**{key: value for key, value in row.items() if key != "observations"},
                     "citation": f"H{number}"})
    return {"mode": data["mode"], "coverage": data["coverage"], "rows": rows}


def _iso_date(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use YYYY-MM-DD")
    return date.fromisoformat(value).isoformat()


def sample_lookup(args, **kwargs):
    if not isinstance(args, dict):
        return json.dumps({"error": "Choose a supported fictional topic and source."})
    topic, source = args.get("topic"), args.get("source", "all")
    if topic not in ("fees", "swaps", "coverage") or source not in ("all", "kraken", "wallet"):
        return json.dumps({"error": "Choose a supported fictional topic and source."})
    try:
        start, end = _iso_date(args.get("start")), _iso_date(args.get("end"))
        if start and end and start > end:
            raise ValueError("Reversed date range")
    except ValueError:
        return json.dumps({"error": "Choose a valid UTC date range."})
    data = sample_data()
    rows = []
    for row in data["rows"]:
        day = row["at"][:10]
        group = "kraken" if row["id"].startswith("kraken:") else "wallet"
        if (source != "all" and source != group) or (start and day < start) or (end and day > end):
            continue
        if topic == "fees" and row["fee_asset"]:
            rows.append({key: row[key] for key in ("id", "at", "fee_asset", "fee", "citation", "evidence")})
        if topic == "swaps" and row["kind"] == "defi_swap":
            rows.append({key: row[key] for key in ("id", "at", "asset", "quantity", "citation", "evidence")})
    return json.dumps({"heading": "Fictional sample", "mode": data["mode"], "coverage": data["coverage"], "rows": rows,
                       "scope": {"source": source, "start": start, "end": end}})

def register(ctx):
    from . import alert_schedule, ask_chat, briefing, companion, year_review, support
    from .public_chain import run, setup
    from .storage_proof import register_storage
    from .sample_chat import register_sample_chat

    register_storage(ctx)
    register_sample_chat(ctx)
    support.register(ctx)
    ask_chat.register(ctx)
    briefing.register(ctx)
    ctx.register_cli_command("holdings-ethereum", "Inspect bounded public Ethereum evidence",
                             setup_fn=setup, handler_fn=run)
    ctx.register_command("holdings-explain-year", year_review.slash,
                         description="Explain a year of bundled fictional Holdings activity with cited evidence.")
    companion.register_tools(ctx)
    alert_schedule.register(ctx)
    ctx.register_tool(name="holdings_sample_lookup", toolset="holdings_sample",
                      schema=SAMPLE_TOOL, handler=sample_lookup)
