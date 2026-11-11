"""Operator-invoked public Ethereum evidence. No model or storage access."""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .ethereum import ADDR, ETHEREUM_ASSETS, bounded_history, inspect_swap, read_only_rpc, wallet_balances
from .ledger import as_units
from . import solana_balances

CHAIN = "https://ethereum-rpc.publicnode.com"
FACTORY_READ = "https://eth.drpc.org"
FACTORY_FALLBACK = "https://rpc.mevblocker.io"
HISTORY = "https://api.etherscan.io/v2/api"
MAX_BYTES = 2 * 1024 * 1024
# Keyless wallet reads cover a short curated list per network. Each contract and its decimals were
# checked against the token contract's own symbol() and decimals() and against DefiLlama coin metadata.
# Every row costs one eth_call, so the list stays short (at most ten rows per network).
WALLET_NETWORKS = {
    1: ("Ethereum", CHAIN, ETHEREUM_ASSETS + (
        ("WBTC", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", 8),
        ("USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
        ("DAI", "0x6b175474e89094c44da98b954eedeac495271d0f", 18),
        ("LINK", "0x514910771af9ca656af840dff83e8264ecf986ca", 18),
        ("UNI", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", 18),
        ("stETH", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", 18),
        ("wstETH", "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0", 18))),
    8453: ("Base", "https://base-rpc.publicnode.com", (
        ("ETH", None, 18), ("WETH", "0x4200000000000000000000000000000000000006", 18),
        ("USDC", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 6),
        ("cbBTC", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", 8),
        ("DAI", "0x50c5725949a6f0c72e6c4a641f24049a917db0cb", 18),
        ("cbETH", "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22", 18),
        ("wstETH", "0xc1cba3fcea344f92d9239c08c0568f6f2f0ee452", 18),
        ("AERO", "0x940181a94a35a4569e4529a3cdfb74e38fd98631", 18))),
    42161: ("Arbitrum One", "https://arbitrum-one-rpc.publicnode.com", (
        ("ETH", None, 18), ("WETH", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1", 18),
        ("USDC", "0xaf88d065e77c8cc2239327c5edb3a432268e5831", 6),
        ("USDT0", "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9", 6),
        ("WBTC", "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f", 8),
        ("DAI", "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1", 18),
        ("ARB", "0x912ce59144191c1204e64559fe8253a0e49e6548", 18),
        ("LINK", "0xf97f4df75117a78c1a5a0dbb814af92458539fb4", 18))),
    56: ("BNB Chain", "https://bsc-rpc.publicnode.com", (
        ("BNB", None, 18), ("WBNB", "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", 18),
        ("Binance-Peg USDC", "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", 18),
        ("Binance-Peg USDT", "0x55d398326f99059ff775485246999027b3197955", 18),
        ("BTCB", "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c", 18),
        ("Binance-Peg ETH", "0x2170ed0880ac9a755fd29b2688956bd959f933f8", 18),
        ("CAKE", "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", 18),
        ("FDUSD", "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409", 18))),
    4663: ("Robinhood Chain", "https://robinhood-rpc.publicnode.com", (
        ("ETH", None, 18), ("WETH", "0x0bd7d308f8e1639fab988df18a8011f41eacad73", 18),
        ("USDG", "0x5fc5360d0400a0fd4f2af552add042d716f1d168", 6))),
}
# PublicNode serves the pinned head for these L2s, but rejects historical safe-block calls.
WALLET_HEAD_ONLY = {42161, 4663}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Minimum gap between request starts to one provider host, shared by every read in this process.
# Etherscan Free allows 3 calls per second, Zerion Developer allows 3 per second and the Solana
# public endpoint allows 40 calls of one method per 10 seconds. Hosts that publish no number,
# such as the PublicNode RPCs and DefiLlama, get at most 10 requests per second.
PROVIDER_SPACING = {"api.etherscan.io": 0.35, "api.zerion.io": 0.35, "api.mainnet-beta.solana.com": 0.25}
DEFAULT_SPACING = 0.1
_pace_lock = threading.Lock()
_next_slot = {}


def pace(url, *, clock=time.monotonic, sleep=time.sleep):
    host = (urlsplit(url).hostname or "").lower()
    with _pace_lock:
        now = clock()
        slot = max(now, _next_slot.get(host, now))
        _next_slot[host] = slot + PROVIDER_SPACING.get(host, DEFAULT_SPACING)
    if slot > now:
        sleep(slot - now)


# A 429 from one host pauses every request to that host for Retry-After seconds (60 by default,
# between 5 and 300), so the rest of a read fails fast instead of adding load.
RATE_LIMIT_PAUSE = 60
_cooldown = {}


def _retry_after(value):
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return RATE_LIMIT_PAUSE
    return min(300, max(5, seconds))


def rate_limited(url, *, clock=time.monotonic):
    host = (urlsplit(url).hostname or "").lower()
    with _pace_lock:
        return clock() < _cooldown.get(host, 0)


def fetch(url, payload=None, *, timeout=8, opener=None, clock=time.monotonic):
    host = (urlsplit(url).hostname or "").lower()
    if rate_limited(url, clock=clock):
        raise OSError("Public chain provider is pausing after a rate limit")
    pace(url)
    # Another read may have hit a 429 on this host while this one waited for its turn.
    if rate_limited(url, clock=clock):
        raise OSError("Public chain provider is pausing after a rate limit")
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=body, headers={"Accept": "application/json", "Content-Type": "application/json",
                                               "User-Agent": "Holdings-Hermes/0.1"})
    try:
        with (opener or build_opener(NoRedirect())).open(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Provider response exceeded the limit")
        return json.loads(raw)
    except HTTPError as exc:
        if exc.code == 429:
            with _pace_lock:
                _cooldown[host] = clock() + _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
        raise OSError("Public chain provider request failed") from None
    except (OSError, ValueError):
        # The history URL contains a credential. Never propagate provider or URL text.
        raise OSError("Public chain provider request failed") from None


def rpc(url, *, timeout=8, balance_tokens=None, wallet_block="safe"):
    def send(method, params):
        response = fetch(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=timeout)
        if not isinstance(response, dict) or response.get("id") != 1 or "result" not in response:
            raise OSError("Ethereum RPC evidence unavailable")
        return response["result"]
    if balance_tokens is not None:
        return read_only_rpc(send, balance_tokens=balance_tokens, wallet_only=True, wallet_block=wallet_block)
    return read_only_rpc(send)


def factory_rpc(*, timeout=8):
    primary, fallback = (rpc(url, timeout=timeout) for url in (FACTORY_READ, FACTORY_FALLBACK))

    def read(method, params):
        try:
            result = primary(method, params)
            if result is None:
                raise OSError("Factory evidence missing")
            return result
        except OSError:
            return fallback(method, params)

    return read


ETHERSCAN_SPACING = 0.35  # the free Etherscan plan allows 3 calls per second


def etherscan_wallet_balances(get, address, chain_id, assets, pause=time.sleep):
    if chain_id not in {1, 42161} or not ADDR.fullmatch(address):
        raise ValueError("Unsupported Etherscan balance scope")
    balances = []
    for index, (asset, contract, decimals) in enumerate(assets):
        if index:
            pause(ETHERSCAN_SPACING)
        params = {"chainid": chain_id, "module": "account", "tag": "latest", "address": address,
                  "action": "tokenbalance" if contract else "balance"}
        if contract:
            params["contractaddress"] = contract
        response = get(params)
        raw = response.get("result") if isinstance(response, dict) and response.get("status") == "1" else None
        if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,78}", raw) or int(raw) >= 2 ** 256:
            raise ValueError("Etherscan balance unavailable")
        balances.append({"asset": asset, "units": format(as_units(int(raw), decimals), "f")})
    return {"status": "ready", "source": "etherscan", "balances": balances,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def read_wallet_balances(address, key=None, chain_id=1):
    if not isinstance(address, str) or not ADDR.fullmatch(address.lower()) or chain_id not in WALLET_NETWORKS:
        raise ValueError("Invalid wallet scope")
    _, url, assets = WALLET_NETWORKS[chain_id]
    use_key = bool(key) and chain_id in {1, 42161}
    if use_key:
        def get(params):
            return fetch(HISTORY + "?" + urlencode({**params, "apikey": key}), timeout=4)

        try:
            return etherscan_wallet_balances(get, address.lower(), chain_id, assets)
        except (OSError, ValueError):
            pass
    tokens = {contract for _, contract, _ in assets if contract}
    block_tag = "latest" if chain_id in WALLET_HEAD_ONLY else "safe"
    transport = rpc(url, timeout=4, balance_tokens=tokens, wallet_block=block_tag)
    result = wallet_balances(transport, address, chain_id, assets, block_tag)
    return {**result, "source": "publicnode", "fallback": use_key, "blockKind": block_tag}


# One address can cover several networks, so the desktop sends one entry per
# address and network pair. Twelve pairs is the most one GoPlus pass fits in the interactive
# budget (two calls per pair), and every host stays paced by PROVIDER_SPACING.
MAX_WALLET_PAIRS = 12


def wallet_selection(body, *, solana=False):
    if not isinstance(body, dict) or set(body) != {"wallets"}:
        raise ValueError("Invalid wallets")
    wallets = body["wallets"]
    if not isinstance(wallets, list) or not 1 <= len(wallets) <= MAX_WALLET_PAIRS:
        raise ValueError("Choose between one and twelve wallet and chain pairs")
    selection = []
    for wallet in wallets:
        if not isinstance(wallet, dict) or set(wallet) != {"address", "chainId"}:
            raise ValueError("Invalid wallet and chain pair")
        address, chain_id = wallet["address"], wallet["chainId"]
        if solana and chain_id == "solana" and solana_balances.is_pubkey(address):
            pair = (address, chain_id)
        elif (type(chain_id) is int and chain_id in WALLET_NETWORKS and isinstance(address, str)
              and ADDR.fullmatch(address.lower())):
            pair = (address.lower(), chain_id)
        else:
            raise ValueError("Invalid wallet and chain pair")
        if pair in selection:
            raise ValueError("Duplicate wallet and chain pair")
        selection.append(pair)
    return selection


def _read_pair(address, chain_id, key):
    if chain_id == "solana":
        data = solana_balances.snapshot(address, fetch)
        return {**data, "address": address, "chainId": chain_id, "chain": "Solana"}
    name, _, assets = WALLET_NETWORKS[chain_id]
    try:
        data = read_wallet_balances(address, key, chain_id)
    except (OSError, TypeError, ValueError):
        data = {"status": "unavailable"}
    if data["status"] == "ready":
        data["balances"] = [
            {**row, "contract": asset[1], "decimals": asset[2]}
            for row, asset in zip(data["balances"], assets)
        ]
    return {**data, "address": address, "chainId": chain_id, "chain": name}


def _provider_group(chain_id, key):
    if chain_id == "solana":
        return "solana"
    return "etherscan" if key and chain_id in {1, 42161} else chain_id


def read_wallet_selection(selection, key):
    # Pairs that share a provider run one after another, and different providers run side by side,
    # so a full read of twelve pairs never sends one host more than its paced rate.
    groups = {}
    for index, (address, chain_id) in enumerate(selection):
        groups.setdefault(_provider_group(chain_id, key), []).append((index, address, chain_id))
    wallets = [None] * len(selection)

    def run(items):
        for index, address, chain_id in items:
            wallets[index] = _read_pair(address, chain_id, key)

    if len(groups) <= 1:
        for items in groups.values():
            run(items)
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(groups))) as pool:
            for future in [pool.submit(run, items) for items in groups.values()]:
                future.result()
    return {"status": "ready", "wallets": wallets}


def setup(parser):
    actions = parser.add_subparsers(dest="action", required=True)
    swap = actions.add_parser("swap", help="Verify one public Uniswap v3 transaction")
    swap.add_argument("wallet")
    swap.add_argument("transaction")
    history = actions.add_parser("history", help="Read bounded public Ethereum activity")
    history.add_argument("wallet")
    history.add_argument("start_block", type=int)
    history.add_argument("end_block", type=int)
    history.add_argument("--page-size", type=int, default=25)
    history.add_argument("--max-pages", type=int, default=3)


def fetch_history(wallet, start, end, key, page_size, max_pages, *, timeout=8):
    if not 0 <= start <= end or end - start > 10_000:
        raise ValueError("Choose an inclusive range spanning at most 10,001 blocks")

    def get(params):
        return fetch(HISTORY + "?" + urlencode({**params, "apikey": key}), timeout=timeout)

    return bounded_history(get, wallet, start, end, page_size, max_pages)


def history_result(args):
    from agent.secret_scope import get_secret

    key = get_secret("HOLDINGS_ETHERSCAN_API_KEY")
    if not key:
        return {"status": "blocked", "reason": "Set Etherscan API key in this Hermes profile's Holdings settings."}

    evidence, coverage = fetch_history(args.wallet, args.start_block, args.end_block, key,
                                       args.page_size, args.max_pages)
    complete = all(stream["end_of_results"] for stream in coverage["streams"].values())
    rows = {stream: [{"hash": row["hash"], "block": int(row["blockNumber"]),
                      "classification": "unclassified"} for row in values]
            for stream, values in evidence.items()}
    return {"status": "bounded" if complete else "partial", "coverage": coverage, "rows": rows,
            "gaps": ["Internal transfers, NFTs and protocol classification were not checked.",
                     "Rows are provider observations. They are not merged records."]}


def run(args):
    try:
        if args.action == "swap":
            result = inspect_swap(rpc(CHAIN), factory_rpc(), args.wallet, args.transaction)
        else:
            result = history_result(args)
    except ValueError:
        result = {"status": "invalid", "reason": "Check address, transaction hash, block range and page bounds."}
    print(json.dumps(result, indent=2))
