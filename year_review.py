"""Cited, read-only agent view of the bundled fictional year records."""
import json
from datetime import date
from collections import defaultdict
from decimal import Decimal, localcontext

from . import SAMPLE
from .ledger import number
from .transfers import fixture_rows, suggestions

SOURCES = ("kraken-spot", "binance-spot", "ethereum", "bybit-spot", "bybit-csv")
NAME = "holdings_explain_year"
SCHEMA = {
    "name": NAME,
    "description": "Explain a year of bundled fictional Holdings activity, recorded transfers, decisions and coverage gaps. Returns exact amounts and source citations. Does not access private account records, live services or tax calculations.",
    "parameters": {"type": "object", "properties": {
        "year": {"type": "integer", "minimum": 2000, "maximum": 2100},
        "source": {"type": "string", "enum": ["all", *SOURCES]},
    }, "required": ["year"], "additionalProperties": False},
}


def recorded_flows(rows, matched=()):
    totals = defaultdict(lambda: {"in": Decimal(0), "out": Decimal(0), "fees": Decimal(0), "evidence": set()})
    with localcontext() as context:
        context.prec = 250
        for row in rows:
            if row["id"] not in matched and row["kind"] not in {"buy", "sell", "unresolved"}:
                units = number(row["quantity"])
                bucket = totals[row["sourceMode"], row["asset"]]
                bucket["in" if units >= 0 else "out"] += abs(units)
                bucket["evidence"].update(row["evidence"])
            if row["kind"] != "unresolved" and row["fee_asset"] and number(row["fee"]) != 0:
                bucket = totals[row["sourceMode"], row["fee_asset"]]
                bucket["fees"] += number(row["fee"])
                bucket["evidence"].update(row["evidence"])
    return [{"source": name, "asset": asset, "recordedIn": format(total["in"], "f"),
                   "recordedOut": format(total["out"], "f"), "recordedFees": format(total["fees"], "f"),
                   "evidence": sorted(total["evidence"])}
                  for (name, asset), total in sorted(totals.items())]


def report(year, source, data, rows, decisions, decision_available):
    selected = [row for row in rows if row["at"][:4] == str(year)
                and (source == "all" or row["sourceMode"] == source)]
    cases, limited, skipped = suggestions(selected)
    matched = set()
    records = {row["id"]: row for row in selected}
    transfers = []
    for case in cases:
        status = decisions.get(case["id"], {}).get("status", "pending")
        both = (case["outgoing"], case["incoming"])
        if status == "accepted":
            matched.update(both)
        transfers.append({"status": status, "rows": list(both),
                          "evidence": [ref for key in both for ref in records[key]["evidence"]]})
    history = data["history_demo"]
    covered = []
    for name, record in history.items():
        if source not in {"all", name}:
            continue
        start, end = record.get("start"), record.get("end")
        has_rows = any(row["sourceMode"] == name for row in selected)
        dated = isinstance(start, str) and isinstance(end, str)
        if (dated and start[:4] <= str(year) <= end[:4]) or has_rows or start is None:
            covered.append({"source": name, "status": record["status"], "start": start, "end": end,
                            "rangeKind": "UTC dates" if dated else "Ethereum blocks" if has_rows and type(start) is int else "unavailable",
                            "evidence": f"fictional history coverage: {name}"})
    return {
        "mode": "fictional",
        "requestedYearUTC": year,
        "source": source,
        "recordCount": len(selected),
        "flows": recorded_flows(selected, matched),
        "transferReview": {"savedFictionalDecisionsAvailable": decision_available, "items": transfers,
                           "limited": limited, "skipped": skipped},
        "coverage": covered,
        "limitations": [
            "Only the bundled fictional movement sample is included. Demo decisions stay in Desktop.",
            "Accepted links are removed from the flow totals when both legs are in scope. Original source fees remain.",
            "Open or uncertain movements do not establish income, loss or tax treatment. The records do not cover a full year.",
        ],
    }


def accountant_years(body):
    """Read only the rows and explicit decisions already selected for this packet."""
    if not isinstance(body, dict) or set(body) != {"rows", "links"}:
        raise ValueError("Invalid packet scope")
    rows, links = body["rows"], body["links"]
    if not isinstance(rows, list) or not isinstance(links, list) or len(rows) > 3000 or len(links) > 160:
        raise ValueError("Accountant packet exceeds the selected scope limit")
    fields = {"id", "at", "sourceMode", "kind", "asset", "quantity", "fee", "fee_asset", "evidence"}
    by_year, by_id = defaultdict(list), {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError("Invalid accountant row")
        for field in fields - {"evidence"}:
            if not isinstance(row[field], str) or len(row[field]) > 360:
                raise ValueError("Invalid accountant value")
        if date.fromisoformat(row["at"][:10]).isoformat() != row["at"][:10]:
            raise ValueError("Invalid UTC date")
        if (not row["id"] or row["id"] in by_id or row["sourceMode"] not in SOURCES
                or not isinstance(row["evidence"], list) or len(row["evidence"]) > 32
                or any(not isinstance(ref, str) or not 1 <= len(ref) <= 360 for ref in row["evidence"])):
            raise ValueError("Invalid accountant reference")
        number(row["quantity"])
        number(row["fee"])
        by_year[row["at"][:4]].append(row)
        by_id[row["id"]] = row

    matched, transfers = set(), []
    for link in links:
        if (not isinstance(link, dict) or set(link) != {"status", "outgoing", "incoming"}
                or link["status"] not in ("pending", "undone", "rejected", "accepted")):
            raise ValueError("Invalid transfer decision")
        ids = [link["outgoing"], link["incoming"]]
        if any(not isinstance(ref, str) or ref not in by_id for ref in ids) or ids[0] == ids[1]:
            raise ValueError("Transfer is outside the selected rows")
        outgoing, incoming = (by_id[ref] for ref in ids)
        same_year = outgoing["at"][:4] == incoming["at"][:4]
        exclude = link["status"] == "accepted" and same_year
        if exclude:
            kinds = {"withdrawal", "deposit", "transfer", "token_transfer"}
            if (outgoing["kind"] not in kinds or incoming["kind"] not in kinds or
                    number(outgoing["quantity"]) >= 0 or number(incoming["quantity"]) <= 0):
                raise ValueError("Transfer requires an outgoing and incoming record")
            if matched.intersection(ids):
                raise ValueError("A transfer leg cannot be linked twice")
            matched.update(ids)
        transfers.append({"status": link["status"], "records": ids, "excludedFromFlows": exclude,
                          "evidence": outgoing["evidence"] + incoming["evidence"]})

    return {"status": "ready", "years": [{"year": year, "recordCount": len(scoped),
            "flows": recorded_flows(scoped, matched)} for year, scoped in sorted(by_year.items())],
            "transfers": transfers}


def explain_year(args, **kwargs):
    if not isinstance(args, dict):
        return json.dumps({"error": "Choose a fictional year and supported source."})
    year, source = args.get("year"), args.get("source", "all")
    if type(year) is not int or not 2000 <= year <= 2100 or source not in ("all", *SOURCES):
        return json.dumps({"error": "Choose a year from 2000 to 2100 and a supported fictional source."})
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return json.dumps(report(year, source, data, fixture_rows(), {}, False))


def slash(raw):
    if not isinstance(raw, str) or not raw.strip().isascii() or len(raw.strip()) != 4 or not raw.strip().isdigit():
        return "Use /holdings-explain-year 2026 for the fictional year report."
    report = json.loads(explain_year({"year": int(raw.strip())}))
    if "error" in report:
        return report["error"]
    lines = [
        f"Holdings · Fictional year review · {report['requestedYearUTC']} UTC",
        f"{report['recordCount']} bundled records. This sample covers a limited window.",
        "",
        "Coverage and gaps",
        *(f"- {row['source']}: {row['status']} [{row['evidence']}]" for row in report["coverage"]),
        "",
        "Transfer decisions",
        *(f"- {item['status'].title()} [{'; '.join(item['evidence'])}]" for item in report["transferReview"]["items"]),
        "",
        "Recorded flows by source and asset",
        *(f"- {row['source']} · {row['asset']}: in {row['recordedIn']}, out {row['recordedOut']}, "
          f"fees {row['recordedFees']} [{'; '.join(row['evidence'])}]" for row in report["flows"]),
    ]
    if not report["transferReview"]["savedFictionalDecisionsAvailable"]:
        lines.insert(2, "Demo decisions stay in the open Desktop demo.")
    return "\n".join(lines)
