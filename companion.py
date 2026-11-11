"""Bounded, cited reports for the ordinary Hermes chat tool registry."""
import json
from decimal import Decimal, localcontext

from . import SAMPLE, _iso_date, companion_live, year_review
from .transfers import fixture_rows, suggestions

PORTFOLIO_REF = "Holdings fictional portfolio snapshot"


def _reply(mode="fictional", **result):
    return json.dumps({"mode": mode, **result})


def _fixture():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def summary(args, **kwargs):
    if args != {}:
        return _reply(error="This fictional summary does not take arguments.")
    portfolio = _fixture()["portfolio"]
    supplied = sum(row["supplied"] for row in portfolio["positions"])
    debt = sum(row.get("debt", 0) for row in portfolio["positions"])
    return _reply(
        at=portfolio["at"], currency="USD", total=supplied - debt, supplied=supplied, debt=debt,
        allocation=[{"asset": name, "grossValueUsd": value, "evidence": PORTFOLIO_REF}
                    for name, value in portfolio["allocation"]],
        topMovers=None, evidence=[PORTFOLIO_REF],
        limitations=["Illustrative snapshot. Two unpriced collectibles are excluded.",
                     "Asset-level price history is unavailable, so top movers cannot be ranked."])


def exposure(args, **kwargs):
    asset = args.get("asset") if isinstance(args, dict) else None
    if not isinstance(asset, str) or not 1 <= len(asset) <= 16 or set(args) != {"asset"}:
        return _reply(error="Choose a fictional asset such as ETH, WBTC or USDC.")
    portfolio = _fixture()["portfolio"]
    value = next((value for name, value in portfolio["allocation"] if name == asset.upper()), None)
    if value is None:
        return _reply(at=portfolio["at"], coverage="Asset exposure is unavailable in the fictional snapshot.",
                      evidence=[PORTFOLIO_REF])
    with localcontext() as context:
        context.prec = 32
        share = Decimal(value) * 100 / sum(row["supplied"] for row in portfolio["positions"])
        share = format(share.quantize(Decimal("0.01")), "f")
    return _reply(asset=asset.upper(), at=portfolio["at"], grossValueUsd=value, grossSharePercent=share,
                  evidence=[PORTFOLIO_REF], limitations=["Illustrative gross exposure before liabilities."])


def recent_movements(args, **kwargs):
    try:
        if not isinstance(args, dict) or set(args) != {"since"} or args["since"] is None:
            raise ValueError("Date required")
        since = _iso_date(args["since"])
    except ValueError:
        return _reply(error="Provide a UTC date in YYYY-MM-DD format.")
    records = sorted(fixture_rows(), key=lambda row: (row["at"], row["id"]), reverse=True)
    rows = [{key: row[key] for key in ("id", "at", "sourceMode", "kind", "asset", "quantity", "fee", "fee_asset", "evidence")}
            for row in records if row["at"][:10] >= since]
    return _reply(sinceUTC=since, rows=rows, evidence=["Holdings fictional transfer fixture"],
                  limitations=["Original movement legs in the bounded demo. A transfer can appear in both sources."])


def needs_attention(args, **kwargs):
    if args != {}:
        return _reply(error="This fictional queue does not take arguments.")
    rows = fixture_rows()
    evidence = {row["id"]: row["evidence"] for row in rows}
    cases, limited, skipped = suggestions(rows)
    items = []
    for case in cases:
        items.append({"kind": "possible-transfer", "status": "pending", "reason": case["confidence"],
                      "evidence": [*evidence[case["outgoing"]], *evidence[case["incoming"]]]})
    fixture = _fixture()
    for observations in fixture["conflict_demo"].values():
        items.append({"kind": "sample-conflict", "status": "inspect-in-holdings",
                      "evidence": [ref for row in observations for ref in row["evidence"]]})
    for source, record in fixture["history_demo"].items():
        if record["status"] == "unavailable" or any(part["failed_pages"] for part in record.get("coverage", [])):
            items.append({"kind": "coverage-gap", "source": source, "evidence": [f"fictional history coverage: {source}"]})
    return _reply(items=items, limited=limited, skipped=skipped,
                  limitations=["Only the bundled fictional findings. Demo decisions are available only in the open Desktop demo."])


def _etherscan_key():
    from agent.secret_scope import get_secret

    return get_secret("HOLDINGS_ETHERSCAN_API_KEY")


def _call(ctx, topic, handler, params, required, args):
    if not isinstance(args, dict) or not set(args) <= params.keys() or not required <= args.keys():
        return _reply("real", error="Check the tool's required inputs and scope.")
    scope = args.get("scope")
    if scope is None:
        if args.get("address"):
            scope = "public"
        else:
            return _reply("real", status="setup-needed", error=(
                "Open Holdings to add a wallet or exchange. In Hermes chat, provide a public wallet "
                "address or explicitly request saved private data after enabling access in Holdings settings."
            ))
    if scope not in companion_live.SCOPES:
        return _reply("real", error="Choose public or private data, or explicitly request fictional sample data.")
    selected = {key: args[key] for key in required}
    if topic == "explain_year" and "source" in args:
        if scope != "fictional":
            return _reply(scope, error="The source filter is only available for the fictional year report.")
        selected["source"] = args["source"]
    try:
        if topic == "recent_movements" and not _iso_date(selected["since"]):
            raise ValueError
        if topic == "explain_year" and (type(selected["year"]) is not int or not 2000 <= selected["year"] <= 2100):
            raise ValueError
        if topic == "exposure" and (not isinstance(selected["asset"], str) or not 1 <= len(selected["asset"]) <= 24):
            raise ValueError
    except ValueError:
        return _reply(scope, error="Use a valid UTC date, year or asset label.")
    private_enabled = ctx.get_config(companion_live.OPT_IN, False) is True
    if scope == "fictional":
        result = {"heading": "Fictional sample", **json.loads(handler(selected))}
        if not private_enabled:
            result["privateAccess"] = companion_live.ENABLE_NOTICE
        return json.dumps(result)
    if scope == "private":
        if not private_enabled:
            return _reply(scope, status="disabled", privateAccess=companion_live.ENABLE_NOTICE)
        report = companion_live.private_report(topic, selected)
    else:
        report = companion_live.public_report(topic, args, get_key=_etherscan_key)
        if not private_enabled:
            report["privateAccess"] = companion_live.ENABLE_NOTICE
    output = _reply(scope, **report)
    return output if args.get("include_addresses") is True and scope == "public" else companion_live.ADDRESS.sub("[address omitted]", output)


def register_tools(ctx):
    asset = {"type": "string", "description": "Asset label such as ETH, WETH, WBTC or USDC."}
    since = {"type": "string", "description": "First included date in UTC, YYYY-MM-DD."}
    scope = {"type": "string", "enum": list(companion_live.SCOPES), "description":
             "Use public for a public address the user provided. Use private or fictional only at the user's explicit request."}
    public = {
        "address": {"type": "string", "description": "Public wallet address supplied by the user. Required for public scope."},
        "chain_id": {"anyOf": [{"type": "integer"}, {"type": "string", "enum": ["solana"]}], "description": "Numeric supported EVM chain ID, or solana. Defaults to Ethereum mainnet (1)."},
        "include_addresses": {"type": "boolean", "description": "Public scope only. Enable only if the user asks for addresses."},
    }
    block_range = {
        "start_block": {"type": "integer", "description": "First Ethereum mainnet block. Required for public history."},
        "end_block": {"type": "integer", "description": "Last Ethereum mainnet block. Required for public history."},
    }
    year = year_review.SCHEMA["parameters"]["properties"]
    tools = (
        ("summary", "Summarize sourced portfolio value, allocation and mover coverage.", {}, {}, summary),
        ("exposure", "Look up cited exposure or balance observations for one asset.", {"asset": asset}, {}, exposure),
        ("recent_movements", "List bounded cited wallet and exchange movement observations since a UTC date.", {"since": since}, block_range, recent_movements),
        ("needs_attention", "List cited transfer, coverage or wallet security items.", {}, {}, needs_attention),
        ("explain_year", "Explain a bounded UTC year's recorded flows and coverage. Does not compute taxes.",
         {"year": year["year"]}, {**block_range, "source": year["source"]}, year_review.explain_year),
    )
    for topic, description, required, optional, handler in tools:
        name = "holdings_" + topic
        properties = {**required, **optional, "scope": scope, **public}
        schema = {"name": name, "description": description, "parameters": {
            "type": "object", "properties": properties, "required": list(required), "additionalProperties": False,
        }}
        # Hermes may pass tool-runtime metadata in addition to the JSON arguments.
        def dispatch(args, *, topic=topic, handler=handler, properties=properties, required=frozenset(required), **kwargs):
            return _call(ctx, topic, handler, properties, required, args)

        ctx.register_tool(name=name, toolset="holdings_sample", schema=schema, handler=dispatch)
