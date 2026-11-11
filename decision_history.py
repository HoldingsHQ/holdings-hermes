"""Bounded per-record review trail for encrypted snapshots and local exports."""
MAX_EVENTS = 24


def decision_event(record):
    return {key: record[key] for key in ("status", "revision", "updatedAt", "preferred") if key in record}


def trail(record):
    history = record.get("history")
    if history is not None:
        return history, bool(record.get("historyIncomplete"))
    if not record.get("revision"):
        return [], False
    # Older snapshots kept only their latest state. Its prior events are unavailable.
    return [decision_event(record)], record["revision"] > 1


def append_event(previous, record):
    history, incomplete = trail(previous)
    events = [*history, decision_event(record)]
    record["history"] = events[-MAX_EVENTS:]
    record["historyIncomplete"] = incomplete or len(events) > MAX_EVENTS
    return record
