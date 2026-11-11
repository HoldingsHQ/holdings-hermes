"""Durable conflict review without changing the records or original observations."""
import hashlib
import json
from datetime import datetime, timezone

from .decision_history import append_event, trail
from .storage_proof import ProtectedKeyUnavailable, protected_snapshot

MODE = "conflict-decisions"
SOURCES = {"kraken-spot", "binance-spot", "bybit-spot", "bybit-csv"}
MAX_CASES = 80
MAX_DECISIONS = 160


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def _fingerprint(data):
    return hashlib.sha256(data).hexdigest()


def _case(case):
    mode, identity, observations = case["mode"], case["originalId"], case["observations"]
    if (mode not in SOURCES or not isinstance(identity, str) or not 0 < len(identity) <= 240
            or not isinstance(observations, list) or not 2 <= len(observations) <= 20):
        raise ValueError("Invalid conflict case")
    choices = {}
    for row in observations:
        if row["originalId"] != identity:
            raise ValueError("Observation identity differs from the case")
        encoded = _encode(row)
        if len(encoded) > 24 * 1024:
            raise ValueError("Observation exceeds protected review limits")
        choices[_fingerprint(encoded)] = row
    if len(choices) != len(observations):
        raise ValueError("A conflict requires distinct observations")
    key = _fingerprint(_encode([mode, identity, sorted(choices)]))
    return key, choices


def _view(case, key, choices, decision):
    history, incomplete = trail(decision)
    return {
        "id": key, "sourceId": case["mode"] + ":" + case["originalId"],
        "choices": list(choices), "status": decision.get("status", "pending"),
        "preferred": decision.get("preferred"), "revision": decision.get("revision", 0),
        "updatedAt": decision.get("updatedAt"), "history": history, "historyIncomplete": incomplete,
    }


def review(payload):
    mode, action = payload["mode"], payload["action"]
    if mode not in {"combined", "combined-sample"} or action not in {"list", "prefer", "exclude", "undo"}:
        raise ValueError("Invalid conflict action or scope")
    demo = mode == "combined-sample"
    if demo:
        if action != "list":
            raise ValueError("Fictional decisions stay in the Desktop session")
        decisions = {}
    else:
        snapshot, persist = protected_snapshot(MODE)
        snapshot = snapshot or {"mode": MODE, "decisions": {}}
        decisions = snapshot["decisions"]
    if action == "list":
        cases = payload["cases"]
        if not isinstance(cases, list) or len(cases) > MAX_CASES:
            raise ValueError("Narrow the conflict selection")
        items = []
        for case in cases:
            key, choices = _case(case)
            items.append(_view(case, key, choices, decisions.get(key, {})))
        return {"status": "ready", "items": items}
    case = payload["case"]
    key, choices = _case(case)
    if key != payload.get("id"):
        return {"status": "changed"}
    previous = decisions.get(key, {})
    revision = payload.get("revision")
    resolved = previous.get("status") in {"preferred", "excluded"}
    if type(revision) is not int or revision != previous.get("revision", 0) or resolved != (action == "undo"):
        return {"status": "changed"}
    preferred = payload.get("preferred") if action == "prefer" else None
    if action == "prefer" and preferred not in choices:
        raise ValueError("Choose a listed observation")
    if not previous and len(decisions) >= MAX_DECISIONS:
        return {"status": "capacity"}
    decision = {
        "status": {"prefer": "preferred", "exclude": "excluded", "undo": "undone"}[action],
        "preferred": preferred, "revision": revision + 1,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceRecords": previous.get("sourceRecords") or choices,
    }
    decisions[key] = append_event(previous, decision)
    persist(snapshot)
    return {"status": "ready", "item": _view(case, key, choices, decision)}
