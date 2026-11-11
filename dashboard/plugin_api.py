"""Holdings dashboard routes. The sample and help routes serve bundled public content."""
import hashlib
import importlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

router = APIRouter()
SAMPLE = Path(__file__).with_name("sample.json")
permission_lock = Lock()
adapter_lock = Lock()



def parse_json_body(payload):
    """Return a request object or the existing handled malformed-body response."""
    try:
        body = json.loads(payload)
    except (ValueError, TypeError, RecursionError):
        return None, {"status": "invalid"}
    if not isinstance(body, dict):
        return None, {"status": "invalid"}
    return body, None


def load_adapter(module="kraken"):
    # Hermes imports dashboard files independently of the native plugin package.
    root = Path(__file__).resolve().parents[1]
    name = "holdings_dashboard_" + hashlib.sha256(os.fsencode(root)).hexdigest()
    package_file = root / "__init__.py"
    with adapter_lock:
        if name in sys.modules:
            package = sys.modules[name]
            if (getattr(getattr(package, "__spec__", None), "origin", None) != str(package_file)
                    or getattr(package, "__path__", None) != [str(root)]):
                raise ImportError("Holdings dashboard package name is already in use")
        else:
            spec = importlib.util.spec_from_file_location(name, package_file,
                                                          submodule_search_locations=[str(root)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            try:
                spec.loader.exec_module(package)
            except Exception:
                if sys.modules.get(name) is package:
                    del sys.modules[name]
                raise
        adapter = importlib.import_module(f"{name}.{module}")
        if getattr(getattr(adapter, "__spec__", None), "origin", None) != str(root / f"{module}.py"):
            raise ImportError("Holdings dashboard adapter name is already in use")
        return adapter


@router.get("/sample")
def sample():
    return {**json.loads(SAMPLE.read_text(encoding="utf-8")), "support": load_adapter("support").guide(),
            "exchange_demo": load_adapter("demo_exchanges").snapshots()}


@router.get("/support")
def support_guide():
    return load_adapter("support").guide()


def require_local_peer(request):
    # Hermes authenticates mounted plugin routes and binds the request to its profile.
    # This is the additional loopback restriction for Holdings private records.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Use the local Hermes Desktop connection.")


@router.get("/setup/status")
def setup_status(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    from agent.secret_scope import get_secret
    from hermes_constants import get_hermes_home

    configured = any(
        get_secret(f"HOLDINGS_{exchange}_API_KEY") and get_secret(f"HOLDINGS_{exchange}_API_SECRET")
        for exchange in ("KRAKEN", "BINANCE", "BYBIT")
    )
    storage = load_adapter("storage_proof")
    watch_enabled = False
    if (storage.private_folder(get_hermes_home(), create=False) / "alerts-watch-v1.enc").is_file():
        try:
            watch, _ = storage.protected_snapshot("alerts-watch-v1")
            watch_enabled = bool(watch and watch.get("desktopPoll") is True)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            watch_enabled = False
    # Booleans only. Key values never leave Hermes's secret store.
    return {"exchangeConfigured": configured, "alertWatchOn": watch_enabled,
            "zerionConfigured": bool(get_secret("HOLDINGS_ZERION_API_KEY")),
            "etherscanConfigured": bool(get_secret("HOLDINGS_ETHERSCAN_API_KEY"))}


@router.get("/market/kraken-spot-btc-usd")
def kraken_spot_btc_usd(request: Request):
    require_local_peer(request)
    return load_adapter("market").snapshot()


@router.get("/market/kraken-spot-sol-usd")
def kraken_spot_sol_usd(request: Request):
    require_local_peer(request)
    return load_adapter("market").sol_snapshot()


@router.get("/market/kraken-spot-bnb-usd")
def kraken_spot_bnb_usd(request: Request):
    require_local_peer(request)
    return load_adapter("market").bnb_snapshot()


@router.get("/market/ethereum-weth-usdc")
def ethereum_weth_usdc(request: Request):
    require_local_peer(request)
    return load_adapter("market").pool_snapshot()


@router.get("/market/kraken-spot-catalog")
def kraken_spot_catalog(request: Request):
    require_local_peer(request)
    return load_adapter("market").watch_catalog()


@router.get("/market/kraken-spot/{base}")
def kraken_spot_market(base: str, request: Request):
    require_local_peer(request)
    return load_adapter("market").watch_snapshot(base)


@router.post("/kraken/permissions")
def kraken_permissions(request: Request):
    from agent.secret_scope import get_secret

    require_local_peer(request)
    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        api_key = get_secret("HOLDINGS_KRAKEN_API_KEY")
        private_key = get_secret("HOLDINGS_KRAKEN_API_SECRET")
        if not api_key or not private_key:
            return {"status": "missing"}
        adapter = load_adapter()
        adapter.verify_query_permissions(adapter.post_read_only, lambda: time.time_ns() // 1000,
                                         api_key, private_key)
        return {"status": "verified"}
    except (RuntimeError, ValueError):
        return {"status": "unverified"}
    finally:
        permission_lock.release()


def reconcile_local(payload):
    from agent.secret_scope import get_secret

    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        review = load_adapter("kraken_review")
        try:
            review.reopen()
        except FileNotFoundError:
            pass
        except review.ProtectedKeyUnavailable as exc:
            return {"status": exc.status}
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return {"status": "storage-locked"}
        key = get_secret("HOLDINGS_KRAKEN_API_KEY")
        secret = get_secret("HOLDINGS_KRAKEN_API_SECRET")
        if not key or not secret:
            return {"status": "missing-key"}
        try:
            snapshot = review.review(payload, key, secret)
        except (OSError, TypeError, ValueError):
            return {"status": "data-review-blocked"}
        try:
            review.persist(snapshot)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return {"status": "storage-locked"}
        return {"status": "ready", "snapshot": snapshot}
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "blocked"}
    finally:
        permission_lock.release()


@router.post("/kraken/reconcile")
async def kraken_reconcile(request: Request):
    require_local_peer(request)
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 3 * 1024 * 1024:
            return {"status": "too-large"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    return await run_in_threadpool(reconcile_local, body)


@router.post("/kraken/reopen")
def kraken_reopen(request: Request):
    require_local_peer(request)
    try:
        review = load_adapter("kraken_review")
        try:
            snapshot = review.reopen()
        except review.ProtectedKeyUnavailable as exc:
            return {"status": exc.status}
        return {"status": "ready", "snapshot": snapshot}
    except FileNotFoundError:
        return {"status": "empty"}
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return {"status": "storage-locked"}


@router.post("/ethereum/balances")
async def ethereum_balances(request: Request, response: Response):
    from agent.secret_scope import get_secret

    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 256:
            return {"status": "invalid"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    if (not isinstance(body, dict) or set(body) != {"wallet"} or not isinstance(body["wallet"], str)
            or not load_adapter("ethereum").ADDR.fullmatch(body["wallet"].lower())):
        return {"status": "invalid"}

    def read():
        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            return load_adapter("public_chain").read_wallet_balances(
                body["wallet"], get_secret("HOLDINGS_ETHERSCAN_API_KEY"))
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return {"status": "unavailable"}
        finally:
            permission_lock.release()

    return await run_in_threadpool(read)


@router.post("/ethereum/review")
async def ethereum_review(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2048:
            return {"status": "invalid"}
        payload.extend(chunk)
    try:
        params, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    if (not isinstance(params, dict) or set(params) - {"checkOutflows"} != {"wallet", "start", "end", "transaction"}
            or type(params.get("checkOutflows", False)) is not bool):
        return {"status": "invalid"}

    def read():
        from agent.secret_scope import get_secret

        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            key = get_secret("HOLDINGS_ETHERSCAN_API_KEY")
            if not key:
                return {"status": "missing-key"}
            snapshot = load_adapter("ethereum_review").review(
                params["wallet"], params["start"], params["end"], key, transaction=params["transaction"])
            security = load_adapter("wallet_security")
            result = {"status": "ready", "snapshot": snapshot, "lookalikes": security.lookalike_checks(snapshot)}
            if params.get("checkOutflows"):
                result["outflows"] = security.outflow_checks(snapshot)
            return result
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return {"status": "blocked"}
        finally:
            permission_lock.release()

    return await run_in_threadpool(read)


@router.post("/wallets/balances")
async def multichain_wallet_balances(request: Request, response: Response):
    from agent.secret_scope import get_secret

    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2048:
            return {"status": "invalid"}
        payload.extend(chunk)
    adapter = load_adapter("public_chain")
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        selection = adapter.wallet_selection(body, solana=True)
    except (TypeError, ValueError):
        return {"status": "invalid"}

    def read():
        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            result = adapter.read_wallet_selection(selection, get_secret("HOLDINGS_ETHERSCAN_API_KEY"))
            try:
                tracking = load_adapter("performance")
                quote = load_adapter("market").pool_snapshot() if any(pair[1] == 1 for pair in selection) else {}
                prices = load_adapter("token_prices").current(selection)
                points = tracking.wallet_points(selection, result["wallets"], quote, prices)
                views = tracking.record(points)
                result["performance"] = views.get(tracking.scope_id(sorted(selection)))
                for pair, wallet in zip(selection, result["wallets"]):
                    wallet["performance"] = views.get(tracking.scope_id([pair]))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                result["performance"] = {"status": "unavailable"}
            return result
        finally:
            permission_lock.release()

    return await run_in_threadpool(read)


@router.post("/wallets/zerion")
async def zerion_wallet_positions(request: Request, response: Response):
    from agent.secret_scope import get_secret

    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2048:
            return {"status": "invalid"}
        payload.extend(chunk)
    adapter = load_adapter("zerion")
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        if (not isinstance(body, dict) or set(body) != {"wallets", "section"}
                or not isinstance(body["section"], str) or body["section"] not in adapter.SECTIONS):
            return {"status": "invalid"}
        selection = load_adapter("public_chain").wallet_selection({"wallets": body["wallets"]})
    except (TypeError, ValueError):
        return {"status": "invalid"}

    def read():
        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            key = get_secret("HOLDINGS_ZERION_API_KEY")
            if not key:
                return {"status": "missing-key"}
            return adapter.snapshot(selection, body["section"], key)
        finally:
            permission_lock.release()

    return await run_in_threadpool(read)


@router.post("/wallets/security")
async def wallet_security(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 24576:
            return {"status": "invalid"}
        payload.extend(chunk)
    adapter = load_adapter("wallet_security")
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        if body == {"fixture": True}:
            return adapter.fixture()
        if body == {"fixture": "outflow"}:
            return adapter.outflow_fixture()
        if body == {"fixture": "look-alike"}:
            return adapter.lookalike_fixture()
        wallets, contracts = adapter.selection(body)
    except (ValueError, TypeError, RecursionError):
        return {"status": "invalid"}
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}

    def read():
        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            return adapter.snapshot(wallets, contracts)
        finally:
            permission_lock.release()

    return await run_in_threadpool(read)


@router.get("/security/incidents")
def security_incidents(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        return load_adapter("protocol_incidents").snapshot()
    finally:
        permission_lock.release()


@router.get("/security/market-status")
def security_market_status(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        return load_adapter("market_status").snapshot()
    finally:
        permission_lock.release()


@router.post("/alerts/watch")
async def alert_watch(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 4096:
            return {"status": "invalid"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, RecursionError):
        return {"status": "invalid"}
    return await run_in_threadpool(load_adapter("alert_watch").request, body)


@router.post("/performance/cost-basis")
async def performance_cost_basis(request: Request, response: Response):
    from csv import Error as CsvError

    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 256 * 1024:
            return {"status": "invalid"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        if (not isinstance(body, dict) or set(body) != {"csv"} or not isinstance(body["csv"], str)
                or len(body["csv"].encode()) > 64 * 1024):
            return {"status": "invalid"}
        return {"status": "ready", "groups": load_adapter("performance").import_basis(body["csv"])}
    except (CsvError, TypeError, ValueError):
        return {"status": "invalid"}


@router.post("/accountant/year-summary")
async def accountant_year_summary(request: Request, response: Response):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2 * 1024 * 1024:
            return {"status": "too-large"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
        return await run_in_threadpool(load_adapter("year_review").accountant_years, body)
    except (RecursionError, TypeError, ValueError):
        return {"status": "invalid"}


@router.get("/briefing/{mode}")
def weekly_briefing(request: Request, response: Response, mode: str):
    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if mode not in {"sample", "local"}:
        return {"status": "invalid"}
    return load_adapter("briefing").preview(sample=mode == "sample")


def protected_exchange_local(exchange, payload=None, *, reopen=False, csv_only=False):
    from agent.secret_scope import get_secret

    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        review = load_adapter(f"{exchange}_review")
        try:
            snapshot, persist = review.storage(csv_only=True) if csv_only else review.storage()
        except review.ProtectedKeyUnavailable as exc:
            return {"status": exc.status}
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return {"status": "storage-locked"}
        if reopen:
            return {"status": "ready", "snapshot": snapshot} if snapshot else {"status": "empty"}
        try:
            if csv_only:
                snapshot = review.csv_review(payload)
            else:
                key = get_secret(f"HOLDINGS_{exchange.upper()}_API_KEY")
                secret = get_secret(f"HOLDINGS_{exchange.upper()}_API_SECRET")
                if not key or not secret:
                    return {"status": "missing-key"}
                snapshot = review.review(payload, key, secret)
                if not snapshot["coverage"][0]["pages"]:
                    return {"status": "data-review-blocked"}
        except (OSError, TypeError, ValueError):
            return {"status": "data-review-blocked"}
        try:
            persist(snapshot)
        except (OSError, TypeError, ValueError):
            return {"status": "storage-locked"}
        return {"status": "ready", "snapshot": snapshot}
    except (ImportError, RuntimeError):
        return {"status": "blocked"}
    finally:
        permission_lock.release()


@router.post("/binance/review")
async def binance_review(request: Request):
    require_local_peer(request)
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2 * 1024 * 1024:
            return {"status": "too-large"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    return await run_in_threadpool(protected_exchange_local, "binance", body)


@router.post("/binance/reopen")
def binance_reopen(request: Request):
    require_local_peer(request)
    return protected_exchange_local("binance", reopen=True)


def exchange_balances(request, response, exchange):
    from agent.secret_scope import get_secret

    require_local_peer(request)
    response.headers["Cache-Control"] = "no-store"
    if load_adapter("storage_proof").in_plugin_host():
        return {"status": "protected-host-unavailable"}
    if not permission_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        key = get_secret(f"HOLDINGS_{exchange.upper()}_API_KEY")
        secret = get_secret(f"HOLDINGS_{exchange.upper()}_API_SECRET")
        if not key or not secret:
            return {"status": "missing-key"}
        adapter = load_adapter(exchange)
        if exchange == "kraken":
            result = adapter.read_spot_balances(adapter.post_read_only, lambda: time.time_ns() // 1_000, key, secret)
        else:
            transport = adapter.get_balance_read_only if exchange == "bybit" else adapter.get_read_only
            result = adapter.read_spot_balances(transport, lambda: time.time_ns() // 1_000_000, key, secret)
        if result.get("status") == "ready":
            try:
                prices = load_adapter("exchange_prices").quotes(exchange, [row["asset"] for row in result["balances"]])
            except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                prices = {}
            result["prices"] = prices
            try:
                tracking = load_adapter("performance")
                # Kraken's BTC/USD hourly close stays as the fallback when the public ticker has no BTC price.
                quote = load_adapter("market").snapshot() if exchange == "kraken" and "XXBT" not in prices else {}
                stream = tracking.scope_id([exchange, key])
                result["performance"] = tracking.record({
                    stream: tracking.exchange_point(exchange, result, quote, prices),
                })[stream]
            except (ImportError, OSError, RuntimeError, TypeError, ValueError):
                result["performance"] = {"status": "unavailable"}
        return result
    except (ImportError, RuntimeError, TypeError, ValueError):
        return {"status": "unavailable"}
    finally:
        permission_lock.release()


@router.post("/kraken/balances")
def kraken_balances(request: Request, response: Response):
    return exchange_balances(request, response, "kraken")


@router.post("/binance/balances")
def binance_balances(request: Request, response: Response):
    return exchange_balances(request, response, "binance")


@router.post("/bybit/balances")
def bybit_balances(request: Request, response: Response):
    return exchange_balances(request, response, "bybit")


@router.post("/bybit/review")
async def bybit_review(request: Request):
    require_local_peer(request)
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 512:
            return {"status": "too-large"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    return await run_in_threadpool(protected_exchange_local, "bybit", body)


@router.post("/bybit/reopen")
def bybit_reopen(request: Request):
    require_local_peer(request)
    return protected_exchange_local("bybit", reopen=True)

@router.post("/bybit/import")
async def bybit_import(request: Request):
    require_local_peer(request)
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > 2 * 1024 * 1024:
            return {"status": "too-large"}
        payload.extend(chunk)
    try:
        body, error = parse_json_body(payload)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}
    return await run_in_threadpool(protected_exchange_local, "bybit", body, csv_only=True)


@router.post("/bybit/reopen-csv")
def bybit_reopen_csv(request: Request):
    require_local_peer(request)
    return protected_exchange_local("bybit", reopen=True, csv_only=True)


@router.post("/transfers/decisions")
async def transfer_decisions(request: Request):
    return await protected_decisions(request, "transfers")


@router.post("/conflicts/decisions")
async def conflict_decisions(request: Request):
    return await protected_decisions(request, "conflicts")


async def protected_decisions(request, module):
    require_local_peer(request)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 2 * 1024 * 1024:
            return {"status": "too-large"}
        body.extend(chunk)
    try:
        payload, error = parse_json_body(body)
        if error is not None:
            return error
    except (UnicodeError, ValueError):
        return {"status": "invalid"}

    def decide():
        if not permission_lock.acquire(blocking=False):
            return {"status": "busy"}
        try:
            adapter = load_adapter(module)
            try:
                result = adapter.review(payload)
                if result.get("status") == "ready" and payload["mode"] == "combined":
                    load_adapter("briefing").note_attention(module, result)
                return result
            except adapter.ProtectedKeyUnavailable as exc:
                return {"status": exc.status}
            except (ImportError, KeyError, OSError, OverflowError, RuntimeError, TypeError, ValueError):
                return {"status": "blocked"}
        finally:
            permission_lock.release()

    return await run_in_threadpool(decide)
