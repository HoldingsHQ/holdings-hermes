"""Opt-in, fictional integration replay against a real Hermes gateway."""
import argparse
import base64
from contextlib import ExitStack, contextmanager
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from vault import store

FIXTURE_KEY = b"fictional-profile-scope-check!!!"
BINANCE_KEY = "fictional-binance-api-key"
BINANCE_SECRET = "fictional-binance-secret"
KRAKEN_KEY = "fictional-kraken-api-key"
KRAKEN_SECRET = base64.b64encode(b"fictional-kraken-secret").decode()
BYBIT_KEY = "fictional-bybit-api-key"
BYBIT_SECRET = "fictional-bybit-secret"
ETHERSCAN_KEY = "fictional-etherscan-key"
ZERION_KEY = "fictional-zerion-key"
WALLET_READ = {"wallet": "0x" + "11" * 20}
WALLET_FALLBACK = {"wallet": "0x" + "22" * 20}
MULTICHAIN_PAYLOAD = {"wallets": [
    {"address": WALLET_FALLBACK["wallet"], "chainId": 1},
    {"address": WALLET_FALLBACK["wallet"], "chainId": 56},
]}
ZERION_PAYLOAD = {**MULTICHAIN_PAYLOAD, "section": "tokens"}
SECURITY_TOKEN = "0x" + "44" * 20
BALANCE_ROUTES = ("kraken/balances", "binance/balances", "bybit/balances")
REVIEW_PAYLOADS = {
    "transfers/decisions": {"mode": "combined", "action": "list", "rows": []},
    "conflicts/decisions": {"mode": "combined", "action": "list", "cases": []},
}


def run_balance_fixture_gateway():
    """Exercise the real signed transports without contacting exchanges."""
    from ethereum import USDC, WETH
    from tests.test_zerion import token
    from tests import test_market, test_solana

    original = urllib.request.OpenerDirector.open

    def open_fixture(opener, request, *args, **kwargs):
        url = urllib.parse.urlsplit(request.full_url if hasattr(request, "full_url") else request)
        if url.hostname == "api.geckoterminal.com":
            assert request.get_method() == "GET" and request.full_url == test_market.market.POOL_OHLC
            fixture = test_market.PoolMarketTests()
            hour = int(time.time()) // 3600
            body = fixture.payload([fixture.candle(hour - delta) for delta in range(170)])
            return io.BytesIO(json.dumps(body).encode())
        if url.hostname == "api.kraken.com" and url.path.startswith("/0/public/"):
            assert request.get_method() == "GET"
            if url.path == "/0/public/SystemStatus":
                body = {"error": [], "result": {"status": "online"}}
            elif url.path == "/0/public/OHLC":
                pair = urllib.parse.parse_qs(url.query)["pair"][0]
                identity = {v: k for k, v in test_market.market.KRAKEN_PAIRS.items()}[pair]
                assert urllib.parse.parse_qs(url.query)["interval"] == ["60"]
                hour = int(time.time()) // 3600
                body = {"error": [], "result": {identity: [
                    test_market.candle((hour - 1) * 3600), test_market.candle(hour * 3600)], "last": 0}}
            elif url.path == "/0/public/Ticker" and not url.query:
                # Keyless balance pricing asks for every pair at once.
                body = {"error": [], "result": {"XXBTZUSD": {"c": ["100", "1"], "v": ["1", "2"], "t": [1, 2]}}}
            else:
                assert url.path == "/0/public/Ticker"
                pair = urllib.parse.parse_qs(url.query)["pair"][0]
                identity = {v: k for k, v in test_market.market.KRAKEN_PAIRS.items()}[pair]
                body = {"error": [], "result": {identity: {"c": ["100", "1"]}}}
            return io.BytesIO(json.dumps(body).encode())
        if url.hostname == "api.mainnet-beta.solana.com":
            assert request.get_method() == "POST" and not url.query and url.scheme == "https"
            assert request.get_header("Authorization") is None
            call = json.loads(request.data)
            method, params = call["method"], call["params"]
            assert params[0] == test_solana.OWNER
            if method == "getBalance":
                assert params[1] == {"commitment": "finalized"}
                body = test_solana.result(1000000001, 30)
            else:
                assert method == "getTokenAccountsByOwner"
                assert params[2] == {"commitment": "finalized", "minContextSlot": 30, "encoding": "jsonParsed"}
                program = params[1]["programId"]
                assert program in {value for _, value in test_solana.solana.PROGRAMS}
                body = test_solana.result([test_solana.token(test_solana.ACCOUNT, program, "123")], 31)
            return io.BytesIO(json.dumps(body).encode())
        if url.hostname == "api.zerion.io":
            assert url.scheme == "https" and request.get_method() == "GET"
            assert base64.b64decode(request.get_header("Authorization").split()[1]).decode() == ZERION_KEY + ":"
            root = "/v1/wallets/" + WALLET_FALLBACK["wallet"]
            query = urllib.parse.parse_qs(url.query)
            assert query.pop("currency") == ["usd"]
            if url.path == root + "/positions/":
                assert query.pop("filter[positions]") == ["only_simple"]
                chain, = query.pop("filter[chain_ids]")
                assert chain in {"ethereum", "binance-smart-chain"}
                row = token(chain=chain)
                row["attributes"]["price"] = 1
                body = {"data": [row], "links": {}}
            else:
                assert url.path == root + "/portfolio" and not query
                body = {"data": {"type": "portfolio", "attributes": {"positions_distribution_by_chain": {
                    "ethereum": 10, "binance-smart-chain": 11, "unselected": 100}}}}
            response = io.BytesIO(json.dumps(body).encode())
            response.status = 200
            return response
        if url.hostname == "api.gopluslabs.io":
            assert url.scheme == "https" and request.get_method() == "GET"
            assert request.get_header("Authorization") is None
            if url.path == "/api/v1/token_security/1":
                assert urllib.parse.parse_qs(url.query) == {"contract_addresses": [SECURITY_TOKEN]}
                result = {SECURITY_TOKEN: {"is_airdrop_scam": "1", "token_symbol": "FIXTURE"}}
            else:
                assert url.path == "/api/v2/token_approval_security/1"
                assert urllib.parse.parse_qs(url.query) == {"addresses": [WALLET_FALLBACK["wallet"]]}
                result = []
            return io.BytesIO(json.dumps({"code": 1, "result": result}).encode())
        if url.hostname == "coins.llama.fi":
            assert url.path.startswith("/prices/current/") and request.get_method() == "GET" and not url.query
            assert request.get_header("Authorization") is None
            keys = url.path.removeprefix("/prices/current/").split(",")
            assert keys and all(key.startswith(("ethereum:", "bsc:", "coingecko:")) for key in keys)
            coins = {key: {"price": 1.0 if key.endswith(USDC) else 2000.0, "confidence": 0.99,
                           "timestamp": int(time.time()), "symbol": "FICTIONAL", "decimals": 18} for key in keys}
            return io.BytesIO(json.dumps({"coins": coins}).encode())
        if url.hostname == "api.llama.fi":
            assert request.full_url == "https://api.llama.fi/hacks" and request.get_method() == "GET"
            assert request.get_header("Authorization") is None
            report = [{"name": "Fictional protocol", "date": int(time.time()), "chain": ["Base"]}]
            return io.BytesIO(json.dumps(report).encode())
        if url.hostname == "www.federalreserve.gov":
            assert request.full_url == "https://www.federalreserve.gov/feeds/press_monetary.xml"
            assert request.get_method() == "GET" and request.get_header("Authorization") is None
            return io.BytesIO(b'<rss><channel></channel></rss>')
        if url.path in {"/sapi/v1/system/status", "/v5/system/status"}:
            assert request.get_method() == "GET" and not url.query
            assert request.get_header("Authorization") is None
            if url.hostname == "api.binance.com" and url.path == "/sapi/v1/system/status":
                return io.BytesIO(b'{"status":1}')
            assert url.hostname == "api.bytick.com" and url.path == "/v5/system/status"
            return io.BytesIO(b'{"retCode":0,"result":{"list":[]}}')
        if url.hostname == "api.etherscan.io":
            query = {key: value[0] for key, value in urllib.parse.parse_qs(url.query).items()}
            assert url.scheme == "https" and url.path == "/v2/api" and request.get_method() == "GET"
            assert query.pop("apikey") == ETHERSCAN_KEY and query.pop("chainid") == "1"
            assert query.pop("module") == "account" and query.pop("tag") == "latest"
            wallet = query.pop("address")
            if wallet == WALLET_FALLBACK["wallet"]:
                body = {"status": "0", "result": "fictional provider failure"}
            else:
                assert wallet == WALLET_READ["wallet"]
                action = query.pop("action")
                assert action in {"balance", "tokenbalance"}
                amount = {USDC: "1234567", WETH: "2345678901234567890"}.get(query.pop("contractaddress"), "0") if action == "tokenbalance" else "1234567890123456789"
                assert not query
                body = {"status": "1", "result": amount}
            return io.BytesIO(json.dumps(body).encode())
        if url.hostname in {"ethereum-rpc.publicnode.com", "bsc-rpc.publicnode.com"}:
            call = json.loads(request.data)
            method, params = call["method"], call["params"]
            if method == "eth_chainId":
                result = "0x38" if url.hostname == "bsc-rpc.publicnode.com" else "0x1"
            elif method == "eth_getBlockByNumber":
                assert params == ["safe", False]
                result = {"number": "0x123"}
            elif method == "eth_getBalance":
                assert params == [WALLET_FALLBACK["wallet"], "0x123"]
                result = "0x1"
            else:
                assert method == "eth_call" and params[1] == "0x123"
                assert params[0]["data"].endswith(WALLET_FALLBACK["wallet"][2:])
                result = "0x" + "0" * 64
            return io.BytesIO(json.dumps({"id": call["id"], "result": result}).encode())
        if url.hostname == "api.kraken.com":
            assert url.scheme == "https" and request.get_method() == "POST" and not url.query
            assert request.get_header("Api-key") == KRAKEN_KEY
            nonce = urllib.parse.parse_qs(request.data.decode())["nonce"][0]
            digest = hashlib.sha256(nonce.encode() + request.data).digest()
            signature = hmac.digest(base64.b64decode(KRAKEN_SECRET), url.path.encode() + digest, "sha512")
            assert hmac.compare_digest(signature, base64.b64decode(request.get_header("Api-sign")))
            if url.path == "/0/private/GetApiKeyInfo":
                body = {"permissions": ["query-funds", "modify-trades"], "apiKey": KRAKEN_KEY}
            else:
                assert url.path == "/0/private/BalanceEx"
                body = {"XXBT": {"balance": "1.00000000000000000001", "hold_trade": "0.00000000000000000002"}}
            return io.BytesIO(json.dumps({"error": [], "result": body}).encode())
        if url.hostname == "api.bytick.com" and url.path == "/v5/market/tickers":
            assert request.get_method() == "GET" and url.query == "category=spot"
            assert request.get_header("X-bapi-api-key") is None
            return io.BytesIO(json.dumps({"retCode": 0, "time": int(time.time() * 1000), "result": {"list": [
                {"symbol": "BTCUSDC", "lastPrice": "100", "volume24h": "5"}]}}).encode())
        if url.hostname in {"api.bybit.com", "api.bytick.com"}:
            assert url.hostname == "api.bytick.com"
            assert url.scheme == "https" and request.get_method() == "GET"
            assert request.get_header("X-bapi-api-key") == BYBIT_KEY
            timestamp = request.get_header("X-bapi-timestamp")
            window = request.get_header("X-bapi-recv-window")
            signed = f"{timestamp}{BYBIT_KEY}{window}{url.query}".encode()
            assert hmac.compare_digest(request.get_header("X-bapi-sign"), hmac.new(BYBIT_SECRET.encode(), signed, hashlib.sha256).hexdigest())
            if url.path == "/v5/user/query-api":
                assert not url.query
                body = {"readOnly": 0, "uta": 1, "permissions": {"Wallet": ["Withdraw"]}, "apiKey": BYBIT_KEY}
            else:
                assert url.path == "/v5/account/wallet-balance" and url.query == "accountType=UNIFIED"
                body = {"list": [{"accountType": "UNIFIED", "coin": [
                    {"coin": "USDC", "walletBalance": "0.24681012141618202", "spotBorrow": "0.01", "locked": "0.02"},
                ]}]}
            return io.BytesIO(json.dumps({"retCode": 0, "result": body}).encode())
        if url.hostname == "api.binance.com" and url.path in {"/api/v3/ticker/price", "/api/v3/ticker/24hr"}:
            assert request.get_method() == "GET" and request.get_header("X-mbx-apikey") is None
            if url.path == "/api/v3/ticker/price":
                body = [{"symbol": "ETHUSDC", "price": "2"}, {"symbol": "BTCUSDT", "price": "100"}]
            else:
                query = urllib.parse.parse_qs(url.query)
                assert query["type"] == ["MINI"] and json.loads(query["symbols"][0]) == ["ETHUSDC"]
                body = [{"symbol": "ETHUSDC", "lastPrice": "2", "volume": "9", "closeTime": int(time.time() * 1000)}]
            return io.BytesIO(json.dumps(body).encode())
        if url.hostname != "api.binance.com":
            return original(opener, request, *args, **kwargs)
        assert url.scheme == "https" and request.get_method() == "GET"
        assert request.get_header("X-mbx-apikey") == BINANCE_KEY
        query, signature = url.query.rsplit("&signature=", 1)
        assert hmac.compare_digest(signature, hmac.new(BINANCE_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest())
        if url.path == "/sapi/v1/account/apiRestrictions":
            body = {"enableReading": True, "enableSpotAndMarginTrading": True, "enableWithdrawals": True}
        else:
            assert url.path == "/api/v3/account"
            assert urllib.parse.parse_qs(query)["omitZeroBalances"] == ["true"]
            body = {"accountType": "SPOT", "uid": "fixture-discard", "balances": [
                {"asset": "ETH", "free": "0.123456789012345678", "locked": "0.01000000"},
            ]}
        return io.BytesIO(json.dumps(body).encode())

    urllib.request.OpenerDirector.open = open_fixture
    runpy.run_module("hermes_cli.main", run_name="__main__", alter_sys=True)


def control(home, verb, params=None):
    message = {"protocol": 1, "id": 1, "verb": verb, "params": params or {}}
    with socket.socket(socket.AF_UNIX) as peer:
        peer.settimeout(2)
        peer.connect(str(home / "gateway.sock"))
        peer.sendall(json.dumps(message).encode() + b"\n")
        with peer.makefile("rb") as stream:
            reply = json.loads(stream.readline())
    if not reply["ok"]:
        raise ValueError("Hermes rejected the fixture control request")
    return reply["result"]


@contextmanager
def gateway(root, source, archive, label, *, inherited_key=None, override=True, source_enabled=True,
            dotenv_key=False, isolation=None, balance_fixture=False):
    work = root / label
    home, user = work / "h", work / "u"
    addon = home / "plugins" / "holdings"
    addon.mkdir(parents=True)
    user.mkdir()
    sibling = home / "profiles" / "other"
    sibling.mkdir(parents=True)
    fixture_env = "HOLDINGS_VAULT_KEY=" + base64.b64encode(FIXTURE_KEY).decode()
    secret_lines = [fixture_env]
    if balance_fixture:
        secret_lines.extend((f"HOLDINGS_BINANCE_API_KEY={BINANCE_KEY}", f"HOLDINGS_BINANCE_API_SECRET={BINANCE_SECRET}"))
        secret_lines.extend((f"HOLDINGS_KRAKEN_API_KEY={KRAKEN_KEY}", f"HOLDINGS_KRAKEN_API_SECRET={KRAKEN_SECRET}"))
        secret_lines.extend((f"HOLDINGS_BYBIT_API_KEY={BYBIT_KEY}", f"HOLDINGS_BYBIT_API_SECRET={BYBIT_SECRET}"))
        secret_lines.extend((f"HOLDINGS_ETHERSCAN_API_KEY={ETHERSCAN_KEY}", f"HOLDINGS_ZERION_API_KEY={ZERION_KEY}"))
    config = {
        "gateway": {"multiplex_profiles": False},
        "plugins": {"enabled": ["holdings"]},
        "secrets": {"command": {
            "enabled": source_enabled,
            "command": "printf '%s\\n' " + " ".join(map(shlex.quote, secret_lines)),
            "override_existing": override,
        }},
        "model": {"provider": "custom", "default": "auth-fixture", "base_url": "http://127.0.0.1:1/v1"},
    }
    if isolation is not None:
        config["plugins"]["isolation"] = isolation
    for folder, marker in ((home, label), (sibling, label + "-sibling")):
        (folder / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
        # The default profile starts in the old folder, so the gateway replays the one-time move.
        private = folder / "holdings-private" if folder == home else folder / "plugin-data" / "holdings"
        private.parent.mkdir(exist_ok=True)
        private.mkdir(mode=0o700)
        scope = hashlib.sha256(os.fsencode(folder.resolve())).digest()
        store(private / "kraken-spot.enc", json.dumps({"mode": "kraken-spot", "fictional_marker": marker}).encode(),
              key=FIXTURE_KEY, scope=scope)
        for mode in ("binance-spot", "bybit-spot", "bybit-csv"):
            store(private / f"{mode}.enc",
                  json.dumps({"mode": mode, "fictional_marker": marker}).encode(),
                  key=FIXTURE_KEY, scope=hashlib.sha256(scope + mode.encode()).digest())
        for mode in ("transfer-decisions", "conflict-decisions"):
            store(private / f"{mode}.enc", json.dumps({"mode": mode, "decisions": {}}).encode(),
                  key=FIXTURE_KEY, scope=hashlib.sha256(scope + mode.encode()).digest())
    if dotenv_key:
        # A public fixture value exercises the same no-provenance outcome when
        # an existing key wins. Production configuration must not do this.
        (home / ".env").write_text(fixture_env + "\n", encoding="utf-8")
    # The archive comes from the exact local Git revision under test.
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        snapshot.extractall(addon, filter="data")
    env = {"HOME": str(user), "HERMES_HOME": str(home),
           "HERMES_SHARED_AUTH_DIR": str(work / "auth"),
           "PATH": "/usr/bin:/bin", "PYTHONPATH": str(source),
           # Replay the provisioned interpreter; dependency installation is a separate gate.
           "HERMES_DISABLE_LAZY_INSTALLS": "1"}
    if inherited_key is not None:
        env["HOLDINGS_VAULT_KEY"] = base64.b64encode(inherited_key).decode()
    command = [sys.executable, "-B", "-m", "hermes_cli.main", "--profile", "default", "gateway", "run"]
    cwd = source
    if balance_fixture:
        command[2:4] = ["-c", "from tests.probe_hermes_auth import run_balance_fixture_gateway; run_balance_fixture_gateway()"]
        cwd = Path(__file__).resolve().parents[1]
    with (work / "gateway.log").open("w") as log:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log)
        try:
            for _ in range(250):
                try:
                    descriptor = control(home, "identify")
                    if descriptor["state"] == "ready":
                        break
                except (OSError, ValueError):
                    pass
                if process.poll() is not None:
                    raise RuntimeError("Fictional Hermes gateway exited")
                time.sleep(.1)
            else:
                raise RuntimeError("Fictional Hermes gateway did not become ready")

            def ticket():
                args = {"profile_id": str(home.resolve()), "instance_id": descriptor["instance_id"],
                        "purpose": "native-http"}
                return control(home, "session-ticket", args)["ticket"]

            def request(action="reopen", query="", token=None, headers=None, payload=None, method="POST"):
                fields = {"Content-Type": "application/json", **(headers or {})}
                if token is not None:
                    fields["X-Hermes-Gateway-Ticket"] = token
                path = action if "/" in action else "kraken/" + action
                url = descriptor["api_origin"] + "/api/plugins/holdings/" + path + query
                data = json.dumps(payload or {}).encode() if method == "POST" else None
                req = urllib.request.Request(url, data=data, headers=fields, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=10) as response:
                        if action.endswith("/balances") and isolation != "host":
                            assert response.headers.get("Cache-Control") == "no-store"
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, None

            yield request, ticket
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hermes_source", type=Path)
    parser.add_argument("revision", nargs="?")
    parser.add_argument("--archive", type=Path, help="Exact clean source tar for an uncommitted candidate")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    if (args.archive is None) == (args.revision is None):
        parser.error("provide exactly one Git revision or --archive")
    archive = (args.archive.read_bytes() if args.archive else
               subprocess.check_output(["git", "archive", args.revision], cwd=repository))
    source = args.hermes_source.resolve()
    with tempfile.TemporaryDirectory(prefix="holdings-auth-", dir="/tmp") as temp, ExitStack() as stack:
        root = Path(temp).resolve()
        # Desktop starts a detached child with its own profile's already-resolved value.
        # A cold child must fetch the authoritative key even if the inherited value is stale.
        a, ticket_a = stack.enter_context(gateway(root, source, archive, "a", inherited_key=bytes(32)))
        b, ticket_b = stack.enter_context(gateway(root, source, archive, "b", inherited_key=FIXTURE_KEY))
        for request, mint in ((a, ticket_a), (b, ticket_b)):
            for action in (*BALANCE_ROUTES, "reopen", "permissions", "reconcile", "ethereum/balances", "wallets/balances", "wallets/zerion", "wallets/security", "performance/cost-basis", "ethereum/review", "binance/review", "binance/reopen", "bybit/review", "bybit/reopen", "bybit/import", "bybit/reopen-csv", "transfers/decisions", "conflicts/decisions"):
                assert request(action)[0] == 401
                assert request(action, "?profile=other", mint())[0] == 403
            for market in ("market/kraken-spot-btc-usd", "market/kraken-spot-sol-usd", "market/kraken-spot-bnb-usd", "market/ethereum-weth-usdc", "briefing/local", "briefing/sample", "security/incidents", "security/market-status"):
                assert request(market, method="GET")[0] == 401
                assert request(market, "?profile=other", mint(), method="GET")[0] == 403
            assert request(query="?profile=default&profile=other", token=mint())[0] == 403
            assert request(token=mint(), headers={"Origin": "https://fixture.invalid"})[0] == 401
            one_use = mint()
            assert request(token=one_use)[0] == 200
            assert request(token=one_use)[0] == 401
        for route, identity in (("sol", "SOLUSD"), ("bnb", "BNBUSD")):
            path = "market/kraken-spot-" + route + "-usd"
            assert a(path, method="GET", token=ticket_b())[0] == 401
            status, data = a(path, method="GET", token=ticket_a())
            assert status == 200 and data["status"] == "ready" and data["market"] == identity
        status, fictional = a("briefing/sample", method="GET", token=ticket_a())
        assert status == 200 and fictional["fictional"] is True
        assert a("briefing/local", method="GET", token=ticket_b())[0] == 401
        assert a("setup/status", method="GET")[0] == 401
        assert a("setup/status", method="GET", token=ticket_b())[0] == 401
        assert a("setup/status", method="GET", token=ticket_a()) == (
            200, {"exchangeConfigured": False, "alertWatchOn": False, "zerionConfigured": False, "etherscanConfigured": False})
        assert a(token=ticket_a()) == (200, {"status": "ready", "snapshot": {"mode": "kraken-spot", "fictional_marker": "a"}})
        assert b(token=ticket_b()) == (200, {"status": "ready", "snapshot": {"mode": "kraken-spot", "fictional_marker": "b"}})
        for marker, request, own, other in (("a", a, ticket_a, ticket_b), ("b", b, ticket_b, ticket_a)):
            assert request("wallets/balances", token=other(), payload=MULTICHAIN_PAYLOAD)[0] == 401
            assert request("wallets/zerion", token=other(), payload=ZERION_PAYLOAD)[0] == 401
            assert request("wallets/security", token=other(), payload={"fixture": True})[0] == 401
            status, check = request("wallets/security", token=own(), payload={"fixture": True})
            assert status == 200 and check["fictional"] and len(check["wallets"][0]["approvals"]["findings"]) == 1
            status, outflows = request("wallets/security", token=own(), payload={"fixture": "outflow"})
            assert status == 200 and outflows["findings"][0]["severity"] == "critical"
            status, lookalikes = request("wallets/security", token=own(), payload={"fixture": "look-alike"})
            assert status == 200 and lookalikes["findings"][0]["known"][0]["record"] == "fictional-prior-usdc-transfer"
            assert request("ethereum/review", token=own(), payload={
                "wallet": WALLET_READ["wallet"], "start": "1", "end": "1", "transaction": "", "checkOutflows": "yes"}) == (
                    200, {"status": "invalid"})
            assert request("wallets/zerion", token=own(), payload=ZERION_PAYLOAD) == (200, {"status": "missing-key"})
            assert request("wallets/zerion", token=own(), payload={**ZERION_PAYLOAD, "section": "/invalid"}) == (
                200, {"status": "invalid"})
            repeated = MULTICHAIN_PAYLOAD["wallets"][:1] * 2
            assert request("wallets/balances", token=own(), payload={"wallets": repeated}) == (200, {"status": "invalid"})
            too_many = [{"address": "0x" + format(index, "040x"), "chainId": 1} for index in range(1, 14)]
            assert request("wallets/balances", token=own(), payload={"wallets": too_many}) == (200, {"status": "invalid"})
            assert request("ethereum/balances", token=own(), payload={"wallet": "invalid"}) == (200, {"status": "invalid"})
            assert request("ethereum/balances", token=other())[0] == 401
            for route in BALANCE_ROUTES:
                assert request(route, token=own()) == (200, {"status": "missing-key"})
                assert request(route, token=other())[0] == 401
                assert request(route, token=own(), headers={"Origin": "https://fixture.invalid"})[0] == 401
            for exchange in ("binance", "bybit"):
                assert request(f"{exchange}/reopen", token=own()) == (
                    200, {"status": "ready", "snapshot": {"mode": f"{exchange}-spot", "fictional_marker": marker}})
                assert request(f"{exchange}/reopen", token=other())[0] == 401
                assert request(f"{exchange}/review", token=own()) == (200, {"status": "missing-key"})
            assert request("bybit/reopen-csv", token=own()) == (
                200, {"status": "ready", "snapshot": {"mode": "bybit-csv", "fictional_marker": marker}})
            assert request("bybit/reopen-csv", token=other())[0] == 401
            assert request("bybit/import", token=own()) == (200, {"status": "data-review-blocked"})
            transfer_payload = REVIEW_PAYLOADS["transfers/decisions"]
            assert request("transfers/decisions", token=own(), payload=transfer_payload) == (
                200, {"status": "ready", "items": [], "limited": False, "skipped": 0})
            assert request("transfers/decisions", token=other(), payload=transfer_payload)[0] == 401
            assert request("transfers/decisions", token=own(),
                           headers={"Origin": "https://fixture.invalid"}, payload=transfer_payload)[0] == 401
            token = own()
            assert request("transfers/decisions", token=token, payload=transfer_payload)[0] == 200
            assert request("transfers/decisions", token=token, payload=transfer_payload)[0] == 401
            conflict_payload = REVIEW_PAYLOADS["conflicts/decisions"]
            assert request("conflicts/decisions", token=other(), payload=conflict_payload)[0] == 401
            assert request("conflicts/decisions", token=own(),
                           headers={"Origin": "https://fixture.invalid"}, payload=conflict_payload)[0] == 401
            token = own()
            assert request("conflicts/decisions", token=token, payload=conflict_payload) == (
                200, {"status": "ready", "items": []})
            assert request("conflicts/decisions", token=token, payload=conflict_payload)[0] == 401
        assert b(token=ticket_a())[0] == 401
        assert a(token=ticket_b())[0] == 401
        with gateway(root, source, archive, "env-only", dotenv_key=True, source_enabled=False) as (request, mint):
            for route, payload in REVIEW_PAYLOADS.items():
                assert request(route, token=mint(), payload=payload) == (200, {"status": "key-source-unavailable"})
                sample = {**payload, "mode": "combined-sample"}
                expected = {"status": "ready", "items": []}
                if route == "transfers/decisions":
                    expected.update(limited=False, skipped=0, sessionOnly=True)
                    assert request(route, token=mint(), payload={**sample, "rows": {}}) == (200, {"status": "blocked"})
                assert request(route, token=mint(), payload=sample) == (200, expected)
                assert request(route, token=mint(), payload={**sample, "action": "accept"}) == (200, {"status": "blocked"})
        with gateway(root, source, archive, "host", isolation="host") as (request, mint):
            routes = (*BALANCE_ROUTES, "kraken/reopen", "kraken/reconcile", "binance/reopen", "binance/review", "bybit/reopen",
                      "bybit/review", "bybit/reopen-csv", "bybit/import", "conflicts/decisions")
            for route in routes:
                payload = REVIEW_PAYLOADS.get(route)
                assert request(route, payload=payload)[0] == 401
                assert request(route, token=mint(), payload=payload) == (200, {"status": "protected-host-unavailable"})
            for route in ("ethereum/balances", "wallets/balances", "wallets/zerion", "performance/cost-basis", "ethereum/review"):
                assert request(route, payload=WALLET_READ)[0] == 401
                assert request(route, token=mint(), payload=WALLET_READ) == (200, {"status": "protected-host-unavailable"})
            security_scope = {"wallets": MULTICHAIN_PAYLOAD["wallets"][:1], "contracts": [[SECURITY_TOKEN]]}
            assert request("wallets/security", token=mint(), payload=security_scope) == (200, {"status": "protected-host-unavailable"})
            sample = {**REVIEW_PAYLOADS["transfers/decisions"], "mode": "combined-sample"}
            assert request("transfers/decisions", token=mint(), payload=sample) == (200, {
                "status": "ready", "items": [], "limited": False, "skipped": 0, "sessionOnly": True,
            })
            assert request("transfers/decisions", token=mint(), payload={**sample, "mode": "combined"}) == (
                200, {"status": "protected-host-unavailable"})
            assert request("transfers/decisions", token=mint(), payload={**sample, "action": "accept"}) == (
                200, {"status": "blocked"})
        with gateway(root, source, archive, "balance-fixture", balance_fixture=True) as (request, mint):
            status, body = request("ethereum/balances", token=mint(), payload=WALLET_READ)
            assert status == 200 and body["status"] == "ready" and body["source"] == "etherscan"
            assert "block" not in body and [row["units"] for row in body["balances"]][:3] == [
                "1.234567890123456789", "2.345678901234567890", "1.234567"]
            assert len(body["balances"]) == 10 and all(float(row["units"]) == 0 for row in body["balances"][3:])
            status, body = request("ethereum/balances", token=mint(), payload=WALLET_FALLBACK)
            assert status == 200 and body["source"] == "publicnode" and body["fallback"] is True and body["block"] == "291"
            status, body = request("wallets/balances", token=mint(), payload=MULTICHAIN_PAYLOAD)
            assert status == 200 and body["status"] == "ready"
            assert [wallet["chainId"] for wallet in body["wallets"]] == [1, 56]
            assert [wallet["fallback"] for wallet in body["wallets"]] == [True, False]
            assert body["performance"]["status"] == "saved"
            assert len(body["performance"]["points"]) == 1
            status, basis = request("performance/cost-basis", token=mint(), payload={"csv":
                "source,asset_id,acquired_utc,quantity,cost_usd,evidence_id\nkraken,kraken-asset:XXBT,2026-11-01,0.125,13.25,lot-fixture\n"})
            assert status == 200 and basis["status"] == "ready" and basis["groups"][0]["costUsd"] == "13.25"
            assert request("performance/cost-basis", token=mint(), payload={"csv": "bad"}) == (200, {"status": "invalid"})
            [stable] = [row for row in body["wallets"][1]["balances"] if row["asset"] == "Binance-Peg USDC"]
            assert stable["asset"] == "Binance-Peg USDC" and stable["decimals"] == 18
            status, body = request("wallets/zerion", token=mint(), payload=ZERION_PAYLOAD)
            assert status == 200 and body["status"] == "ready"
            assert [wallet["reference"]["value"] for wallet in body["wallets"]] == ["10", "11"]
            assert all(wallet["state"] == "complete" and len(wallet["rows"]) == 1 for wallet in body["wallets"])
            status, check = request("wallets/security", token=mint(), payload={
                "wallets": MULTICHAIN_PAYLOAD["wallets"][:1], "contracts": [[SECURITY_TOKEN]]})
            assert status == 200 and check["wallets"][0]["tokens"]["findings"][0]["title"] == "Possible scam airdrop"
            assert check["wallets"][0]["approvals"]["state"] == "ready"
            status, incidents = request("security/incidents", method="GET", token=mint())
            assert status == 200 and incidents["incidents"][0]["chainIds"] == [8453]
            status, market_status = request("security/market-status", method="GET", token=mint())
            assert status == 200 and [row["severity"] for row in market_status["exchanges"]] == ["info", "warning", "info"]
            solana_scope = {"wallets": [{"address": "1" * 31 + "2", "chainId": "solana"}]}
            status, body = request("wallets/balances", token=mint(), payload=solana_scope)
            assert status == 200 and body["status"] == "ready"
            [wallet] = body["wallets"]
            assert wallet["source"] == "solana" and wallet["chainId"] == "solana"
            assert [row["slot"] for row in wallet["balances"]] == ["30", "31", "31"]
            assert request("wallets/zerion", token=mint(), payload={**solana_scope, "section": "tokens"}) == (
                200, {"status": "invalid"})
            for exchange, expected in (
                ("binance", {"asset": "ETH", "free": "0.123456789012345678", "locked": "0.01000000"}),
                ("kraken", {"asset": "XXBT", "free": "0.99999999999999999999", "locked": "0.00000000000000000002"}),
                ("bybit", {"asset": "USDC", "free": "0.21681012141618202", "locked": "0.02"}),
            ):
                route = f"{exchange}/balances"
                token = mint()
                status, body = request(route, token=token)
                assert status == 200
                assert set(body) == {"status", "permissionStatus", "balances", "fetchedAt", "performance", "prices"}
                assert set(body["prices"]) == {expected["asset"]}
                assert body["performance"]["status"] == "saved"
                assert (body["status"], body["permissionStatus"]) == ("ready", "broader")
                assert body["balances"] == [expected]
                assert body["fetchedAt"].endswith("+00:00")
                assert request(route, token=token)[0] == 401
    print("Mounted routes rejected anonymous, replayed, foreign-origin, and cross-profile requests.")
    print("Each authorized fictional profile reopened only its own encrypted snapshot.")
    print("Both review routes recovered with an authoritative helper and refused unverified inherited keys.")
    print("Plugin-host mode identified protected review failures and allowed only session-only fictional transfers.")
    print("Default in-process exchange balances signed only the permitted reads and warned on extra permissions.")
    print("Ethereum balances passed bounded keyed reads, exact amounts, RPC fallback and the host-mode gate.")
    print("Zerion passed mounted route authorization, host gating and scoped fictional provider reads.")
    print("Solana passed finalized, keyless reads and was excluded from Zerion scope.")
    print("Performance passed scoped encrypted recording and authenticated local cost-basis parsing.")


if __name__ == "__main__":
    main()
