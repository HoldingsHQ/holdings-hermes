"""Opt-in Cron blueprint and deterministic script output for Holdings watch."""
import json

from . import alert_watch

TOOL = "holdings_alert_watch"
SILENT = "[SILENT]"


def render(result):
    count = result.get("newCount")
    if result.get("status") != "ready" or type(count) is not int or count < 0:
        return "Holdings alert watch could not check for alerts. Open Holdings → Alerts to check the profile and coverage."
    incomplete = "Some enabled checks did not finish. Open Holdings → Alerts to review coverage."
    if count == 0:
        return incomplete if result.get("checksIncomplete") else SILENT
    prefix = "Fictional Holdings alert watch" if result["fictional"] else "Holdings alert watch"
    noun = "alert" if count == 1 else "alerts"
    message = f"{prefix} · {count} new {noun}."
    if result.get("checksIncomplete"):
        return f"{message} {incomplete}"
    return f"{message} Open Holdings → Alerts to review the evidence."


def run(args):
    result = alert_watch.request({"action": "cron"}, allow_cron=True)
    print(render(result))
    return 0


def register(ctx):
    def scheduled(args, **kwargs):
        if args != {} or ctx.current_cron_execution() is None:
            return json.dumps({"error": "This tool runs only in an opted-in Hermes scheduled job."})
        result = alert_watch.request({"action": "cron"}, allow_cron=True)
        return json.dumps({"output": render(result), "status": result["status"]})

    ctx.register_cli_command("holdings-alert-watch", "Evaluate and deliver new Holdings watch alerts",
                             setup_fn=lambda _parser: None, handler_fn=run)
    ctx.register_tool(name=TOOL, toolset="holdings_sample", schema={
        "name": TOOL,
        "description": "Run the Holdings alert watch from an opted-in Hermes Cron job and return a local status or [SILENT].",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }, handler=scheduled)
    ctx.register_automation_blueprint(
        "alert-watch",
        title="Holdings alert watch",
        description="Check the saved Holdings alert rules every 15 minutes. Results appear in this Hermes profile’s local scheduled-job history.",
        schedule_template="*/15 * * * *",
        prompt_template=("Call holdings_alert_watch once with an empty object. Reply with only the returned output string, "
                         "including [SILENT] exactly when that is the output. Do not use other tools or include other text."),
        deliver_default="local",
        tags=["holdings", "alerts"],
    )
