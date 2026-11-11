"""Profile-protected rules and bounded, read-only alert evaluation."""
import hashlib
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from . import ethereum_review, market, market_status, protocol_incidents, public_chain, wallet_security, zerion
from .ledger import number
from .storage_proof import KEY_REASONS, ProtectedKeyUnavailable, private_folder, protected_context, protected_snapshot

MODE = "alerts-watch-v1"
RULES = {
    "security": (True, None), "hacks": (True, None), "outflow": (False, None),
    "price": (False, 5), "exchange": (False, None), "lookalike": (False, None),
}
# Desktop polls every 5 minutes. Each rule runs only when it is due, so slow-moving checks
# do not reread public providers on every poll. "Run enabled checks" runs every rule at once.
POLL_SECONDS = 300
RULE_INTERVALS = {"outflow": 300, "security": 900, "lookalike": 900, "price": 900, "exchange": 900, "hacks": 900}
DUE_SLACK = 5
MAX_ALERTS = 100
MAX_SEEN = 2048

def default_state(mode):
    return {"mode": mode, "wallets": [], "rules": {key: {"enabled": enabled, "muted": False, "threshold": threshold}
            for key, (enabled, threshold) in RULES.items()}, "checks": {}, "seen": [], "conditions": {}, "alerts": [], "checkedAt": None, "desktopPoll": False}


def normalize_state(state):
    """Drop unknown saved rules, including a retired depeg rule, without failing the load."""
    base = default_state(MODE)
    if not isinstance(state, dict):
        return base
    source = state.get("rules")
    source = source if isinstance(source, dict) else {}
    cleaned = {}
    for key, (enabled, threshold) in RULES.items():
        config = source.get(key)
        if not isinstance(config, dict) or type(config.get("enabled")) is not bool or type(config.get("muted")) is not bool:
            cleaned[key] = {"enabled": enabled, "muted": False, "threshold": threshold}
            continue
        item = {"enabled": config["enabled"], "muted": config["muted"], "threshold": None}
        if threshold is not None:
            try:
                _threshold_decimal(config.get("threshold"))
                item["threshold"] = config["threshold"]
            except (TypeError, ValueError):
                item["threshold"] = threshold
        cleaned[key] = item
    state["rules"] = cleaned
    conditions = state.get("conditions")
    state["conditions"] = {key: conditions[key] for key in conditions if key in RULES} if isinstance(conditions, dict) else {}
    runs = state.get("ruleRuns")
    state["ruleRuns"] = {key: value for key, value in runs.items() if key in RULES and _moment(value)} if isinstance(runs, dict) else {}
    if state.get("mode") != MODE:
        state["mode"] = MODE
    return state


def _moment(value):
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) and len(value) <= 40 else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def due_rules(state, now=None):
    now = now or datetime.now(timezone.utc)
    runs = state.get("ruleRuns") or {}
    due = set()
    for name, config in state["rules"].items():
        if not config["enabled"]:
            continue
        last = _moment(runs.get(name))
        if last is None or (now - last).total_seconds() >= RULE_INTERVALS[name] - DUE_SLACK:
            due.add(name)
    return due


def _threshold_decimal(value):
    if type(value) not in (float, int) or not 0.1 <= value <= 100:
        raise ValueError("Use a threshold between 0.1% and 100%")
    # JSON/editor numbers use their decimal spelling, not a float's binary expansion.
    # Keep saved rule shapes unchanged and financial-record parsing strict.
    return number(str(value))


def rule_update(body):
    name, enabled, muted, threshold = (body[key] for key in ("rule", "enabled", "muted", "threshold"))
    if name not in RULES or type(enabled) is not bool or type(muted) is not bool:
        raise ValueError("Invalid alert rule")
    if RULES[name][1] is None:
        if threshold is not None:
            raise ValueError("This rule has no numerical threshold")
    else:
        _threshold_decimal(threshold)
    return name, {"enabled": enabled, "muted": muted, "threshold": threshold}


@contextmanager
def state_lock(home):
    import fcntl

    folder = private_folder(home)
    if folder.is_symlink() or folder.stat().st_mode & 0o077:
        raise ValueError("Protected alert folder is not private")
    fd = os.open(folder / ".alerts-watch.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _alert(rule, identity, title, source, at, evidence, *, severity="warning", fictional=False):
    key = hashlib.sha256(f"{rule}:{identity}:{severity}".encode()).hexdigest()
    return {"id": key, "kind": "watch", "rule": rule, "title": title, "source": source,
            "asset": source, "at": at, "evidence": list(dict.fromkeys(evidence))[:6],
            "severity": severity, "fictional": fictional}


def fixture():
    return [_alert("security", "sample-token", "Fictional token flagged", "Fictional GoPlus evidence",
                   "2026-11-09T12:00:00Z", ["sample:wallet-security:token"], fictional=True)]


def _balance_check(wallets):
    return public_chain.read_wallet_selection(wallets, None)["wallets"] if wallets else []


def _contracts(snapshot):
    return [row["contract"] for row in snapshot.get("balances", []) if row.get("contract")
            and row.get("units") is not None and number(row["units"]) > 0][:32]


def _security(wallets, balances):
    if not wallets:
        return "choose-wallets", []
    report = wallet_security.snapshot(wallets, [_contracts(wallet) for wallet in balances], caller="watch")
    findings = [_alert("security", item["id"], item["title"], "GoPlus wallet security", item["at"],
                       [item["contract"], *item["reasons"]], severity=item["severity"])
                for wallet in report["wallets"] for part in wallet.values() for item in part["findings"]]
    states = {part["state"] for wallet in report["wallets"] for part in wallet.values()}
    return ("ready" if states <= {"ready", "no-tokens"} and all(wallet.get("status") == "ready" for wallet in balances)
            else "partial"), findings


# Zerion counts DeFi position reads against a quarter of the daily quota (500 a day on the free
# Developer plan), so the watch reuses one complete scan for 3 hours and retries others after 15 minutes.
# The incident feed is still matched against those positions each time the rule is due.
HACKS_REFRESH = 10800
HACKS_RETRY = 900
_hacks_cache = {}
_hacks_lock = threading.Lock()


def _defi_positions(selected, key, clock=time.monotonic):
    scope = tuple(selected)
    with _hacks_lock:
        cached = _hacks_cache.get("scan")
        if cached and cached[0] == scope and clock() < cached[1]:
            return cached[2]
    positions = zerion.snapshot(selected, "defi", key)["wallets"]
    complete = len(positions) == len(selected) and all(wallet["state"] == "complete" for wallet in positions)
    with _hacks_lock:
        _hacks_cache["scan"] = (scope, clock() + (HACKS_REFRESH if complete else HACKS_RETRY), positions)
    return positions


def _hacks(wallets, key):
    selected = [pair for pair in wallets if pair[1] in zerion.CHAINS]
    if not selected or not key:
        return "needs-zerion-wallet", []
    positions = _defi_positions(selected, key)
    incidents = protocol_incidents.snapshot()
    if incidents["status"] != "ready":
        return "unavailable", []
    findings = []
    for pair, wallet in zip(selected, positions):
        for row in wallet.get("rows", []):
            protocol = re.sub(r"[-_\s]+", " ", row["protocol"].strip().casefold())
            for incident in incidents["incidents"]:
                name = re.sub(r"[-_\s]+", " ", incident["name"].strip().casefold())
                if protocol and name == protocol and pair[1] in incident["chainIds"]:
                    identity = f"{incident['id']}:{pair[1]}:{pair[0].lower()}:{row['id']}"
                    findings.append(_alert("hacks", identity, f'{incident["name"]} incident matches a watched position',
                                           "DefiLlama and Zerion", incident["at"], [incident["id"], row["id"]], severity="high"))
    return ("ready" if len(positions) == len(wallets) and all(wallet["state"] == "complete" for wallet in positions)
            else "partial"), findings


def _price(balances, threshold):
    if not any(wallet["chainId"] == 1 and market.WETH in _contracts(wallet) for wallet in balances):
        return "no-priced-holding", []
    quote = market.pool_snapshot()
    change = (quote.get("marketChanges") or {}).get("24h")
    if quote.get("status") != "ready" or quote.get("stale") or change is None:
        return "unavailable", []
    if abs(number(change)) < _threshold_decimal(threshold):
        return "ready", []
    return "ready", [_alert("price", "rise" if number(change) > 0 else "drop", "WETH crossed the 24-hour movement threshold",
                            "GeckoTerminal WETH/USD", quote["closedAt"], ["Ethereum Uniswap WETH/USDC hourly candles"])]



def _exchange():
    report = market_status.snapshot()
    findings = [_alert("exchange", item["id"] + ":" + item["detail"], item["title"], item["source"],
                       item["at"], [item["detail"]], severity=item["severity"]) for item in report["signals"]]
    return ("partial" if any(item["severity"] == "unavailable" for item in report["exchanges"]) else "ready"), findings


def _history(wallets, balances, key, requested):
    ethereum = [(pair, wallet) for pair, wallet in zip(wallets, balances) if pair[1] == 1]
    selected = [(pair, wallet) for pair, wallet in ethereum if wallet.get("status") == "ready"]
    if not selected or not key:
        return {name: ("needs-ethereum-history", []) for name in requested}
    # A watched Ethereum wallet whose balance read failed was not scanned, so coverage is partial.
    first = "partial" if len(selected) < len(ethereum) else "ready"
    result = {name: (first, []) for name in requested}
    for pair, wallet in selected:
        try:
            end = int(wallet["block"])
            report = ethereum_review.review(pair[0], max(0, end - 600), end, key)
            for name in requested:
                rows = (wallet_security.outflow_checks(report, caller="watch") if name == "outflow" else wallet_security.lookalike_checks(report))
                for item in rows["findings"]:
                    if item["severity"] == "info":
                        continue
                    ref = item["record"]
                    # The ordinal depends on the scan window; retain the transaction,
                    # recipient, wallet, chain and asset instead of that unstable index.
                    identity = (f"{ref.rsplit(':', 1)[0]}:{item['recipient']}:{pair[1]}:{pair[0].lower()}:{item['asset']}"
                                if name == "outflow" else item["id"])
                    evidence = [ref, *(row["record"] for row in item.get("known", []))]
                    result[name][1].append(_alert(name, identity, item["title"], "Ethereum bounded transfer evidence",
                                                  item["at"], evidence, severity=item["severity"]))
                if (rows.get("limited")
                        or (name == "outflow" and any(item.get("status") not in {"flagged", "unflagged"}
                                                      for item in rows["findings"]))
                        or not all(stream["complete"] for stream in report["coverage"])):
                    result[name] = "partial", result[name][1]
        except (KeyError, OSError, TypeError, ValueError):
            for name in requested:
                result[name] = "partial", result[name][1]
    return result


def evaluate(state, *, get_secret, now=None):
    now = now or datetime.now(timezone.utc)
    due = due_rules(state, now)
    rules = {name: config for name, config in state["rules"].items() if name in due}
    wallets = [tuple(pair) for pair in state["wallets"]]
    balance_rules = {"security", "outflow", "price", "lookalike"}
    balances = _balance_check(wallets) if balance_rules & rules.keys() else []
    calls = {
        "security": lambda: _security(wallets, balances),
        "hacks": lambda: _hacks(wallets, get_secret("HOLDINGS_ZERION_API_KEY")),
        "price": lambda: _price(balances, rules["price"]["threshold"]),
        "exchange": _exchange,
    }
    scans = {}
    for name in calls.keys() & rules.keys():
        try:
            scans[name] = calls[name]()
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            scans[name] = "unavailable", []
    history = {"outflow", "lookalike"} & rules.keys()
    if history:
        scans.update(_history(wallets, balances, get_secret("HOLDINGS_ETHERSCAN_API_KEY"), history))
    checks = {name: status for name, (status, _) in scans.items()}
    findings = [item for _, items in scans.values() for item in items]
    stamp = now.isoformat(timespec="seconds")
    state["ruleRuns"] = {**(state.get("ruleRuns") or {}), **{name: stamp for name in rules}}
    return checks, findings


def ingest(state, findings, checks=None):
    state["seen"] = state["seen"][-MAX_SEEN:]
    seen = set(state["seen"])
    current = {}
    for item in findings:
        rule = item["rule"]
        if rule in {"price", "exchange"}:
            current.setdefault(rule, set()).add(item["id"])
    conditions = state["conditions"]
    added = []
    for finding in findings:
        item = finding
        rule = item["rule"]
        if rule in {"price", "exchange"}:
            condition = item["id"]
            if condition in conditions.get(rule, []):
                continue
            item = {**item, "id": hashlib.sha256(f"{condition}:{state['checkedAt']}".encode()).hexdigest()}
        if rule not in RULES or item["id"] in seen:
            continue
        seen.add(item["id"])
        state["seen"].append(item["id"])
        if len(state["seen"]) > MAX_SEEN:
            seen.discard(state["seen"].pop(0))
        muted = state["rules"].get(rule, {}).get("muted", False)
        state["alerts"].append({**item, "read": muted, "muted": muted})
        if not muted:
            added.append(item)
    for rule in ("price", "exchange"):
        if (checks or {}).get(rule) == "ready":
            conditions[rule] = sorted(current.get(rule, ()))
        elif rule in current:
            conditions[rule] = sorted(set(conditions.get(rule, [])) | current[rule])
    state["alerts"] = state["alerts"][-MAX_ALERTS:]
    return added


def _view(state, new=()):
    return {"status": "ready", "rules": state["rules"], "walletCount": len(state["wallets"]),
            "checks": state["checks"], "alerts": state["alerts"], "new": new,
            "checkedAt": state["checkedAt"], "desktopPoll": state.get("desktopPoll", False),
            "unread": sum(not item.get("read") for item in state["alerts"]), "dedupeFull": len(state["seen"]) >= MAX_SEEN}


def request(body, *, open_store=protected_snapshot, context=protected_context, lock=state_lock, evaluate_live=evaluate,
            get_secret=None, allow_cron=False):
    try:
        action = body["action"]
        if action == "fixture" and set(body) == {"action"}:
            items = fixture()
            return {"status": "ready", "alerts": items, "new": items}
        cron = action == "cron"
        if cron and not allow_cron:
            return {"status": "invalid"}
        if body.get("fictional") is True or (not cron and action not in {
                "read", "rule", "wallets", "run", "desktop", "poll", "mark-read"}):
            return {"status": "invalid"}
        home, _, _ = context()
        with lock(home):
            saved, persist = open_store(MODE)
            prior_rules = set((saved or {}).get("rules") or {})
            prior_conditions = dict((saved or {}).get("conditions") or {})
            state = normalize_state(saved or default_state(MODE))
            migrated = saved is not None and (prior_rules != set(state["rules"]) or prior_conditions != state.get("conditions"))
            last = state["checkedAt"]
            polled_recently = last and (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() < POLL_SECONDS - DUE_SLACK
            if action == "read" or (action == "poll" and (not state.get("desktopPoll") or polled_recently)):
                if migrated:
                    persist(state)
                return _view(state)
            new = []
            if action == "rule":
                name, config = rule_update(body)
                state["rules"][name] = config
            elif action == "desktop":
                if type(body.get("enabled")) is not bool:
                    raise ValueError
                state["desktopPoll"] = body["enabled"]
            elif action == "mark-read":
                identity = body.get("id")
                if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
                    raise ValueError
                for item in state["alerts"]:
                    if item["id"] == identity:
                        item["read"] = True
            elif action == "wallets":
                selection = body["wallets"]
                state["wallets"] = public_chain.wallet_selection({"wallets": selection}, solana=True) if selection else []
                state["checks"] = {}
                state["ruleRuns"] = {}
            else:
                if cron:
                    prior = state.setdefault("cronSeen", [item["id"] for item in state["alerts"]])
                if not (cron and polled_recently):
                    if get_secret is None:
                        from agent.secret_scope import get_secret
                    if action == "run":
                        state["ruleRuns"] = {}
                    checks, items = evaluate_live(state, get_secret=get_secret)
                    state["checkedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    enabled = {name for name, config in state["rules"].items() if config["enabled"]}
                    state["checks"] = {name: status for name, status in {**state["checks"], **checks}.items() if name in enabled}
                    new = ingest(state, items, checks)
                if cron:
                    seen = set(prior)
                    unseen = [item for item in state["alerts"] if item["id"] not in seen]
                    prior.extend(item["id"] for item in unseen)
                    state["cronSeen"] = prior[-MAX_SEEN:]
                    new = [item for item in unseen if not item.get("muted") and not state["rules"].get(item["rule"], {}).get("muted")]
            persist(state)
            if cron:
                return {"status": "ready", "newCount": len(new), "fictional": False,
                        "checksIncomplete": any(status != "ready"
                                                for status in state["checks"].values())}
            return _view(state, new)
    except ProtectedKeyUnavailable as exc:
        reason = getattr(exc, "reason", None)
        return {"status": exc.status, **({"reason": reason} if reason in KEY_REASONS else {})}
    except BlockingIOError:
        return {"status": "busy"}
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "unavailable"}
