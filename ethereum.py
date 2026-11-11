"""Bounded Ethereum acquisition and a deliberately narrow Uniswap v3 proof."""
import re
from datetime import datetime, timezone

if __package__:
    from .ledger import Entry, as_units
else:
    from ledger import Entry, as_units

ADDR = re.compile(r"0x[0-9a-f]{40}")
TX_HASH = re.compile(r"0x[0-9a-f]{64}")
FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
ROUTERS = {"0xe592427a0aece92de3edee1f18e0157c05861564",
           "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45"}
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
TOKENS = {USDC: 6, WETH: 18}
ETHEREUM_ASSETS = (("ETH", None, 18), ("WETH", WETH, TOKENS[WETH]), ("USDC", USDC, TOKENS[USDC]))
QUANTITY = re.compile(r"0x(?:0|[1-9a-f][0-9a-f]{0,63})")
BALANCE_WORD = re.compile(r"0x[0-9a-f]{64}")
SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def read_only_rpc(send, *, balance_tokens=TOKENS, wallet_only=False, wallet_block="safe"):
    """Limit the provider boundary to the implemented public reads."""
    def rpc(method, params):
        reads = {"eth_chainId", "eth_getBlockByNumber", "eth_getBalance", "eth_call"}
        if not wallet_only:
            reads.update(("eth_getTransactionByHash", "eth_getTransactionReceipt"))
        if method not in reads:
            raise ValueError("Unsupported Ethereum RPC method")
        if wallet_only and (
                method == "eth_chainId" and params != []
                or method == "eth_getBlockByNumber" and params != [wallet_block, False]):
            raise ValueError("Unsupported wallet chain or block read")
        if method == "eth_getBalance" and not (
                isinstance(params, list) and len(params) == 2 and isinstance(params[0], str)
                and ADDR.fullmatch(params[0]) and isinstance(params[1], str) and QUANTITY.fullmatch(params[1])):
            raise ValueError("Unsupported Ethereum balance read")
        if method == "eth_call":
            if (not isinstance(params, list) or len(params) != 2 or not isinstance(params[0], dict)
                    or set(params[0]) != {"to", "data"} or not all(isinstance(v, str) for v in params[0].values())
                    or not isinstance(params[1], str) or not QUANTITY.fullmatch(params[1])):
                raise ValueError("Unsupported Ethereum contract read")
            target, data = params[0]["to"], params[0]["data"]
            factory = not wallet_only and target == FACTORY and re.fullmatch(r"0x1698ee82[0-9a-f]{192}", data)
            balance = target in balance_tokens and re.fullmatch(r"0x70a08231[0]{24}[0-9a-f]{40}", data)
            if not (factory or balance):
                raise ValueError("Unsupported Ethereum contract read")
        return send(method, params)
    return rpc


def wallet_balances(rpc, address, chain_id=1, assets=ETHEREUM_ASSETS, block_tag="safe"):
    if not isinstance(address, str) or not ADDR.fullmatch(address.lower()):
        raise ValueError("Invalid Ethereum address")
    wallet = address.lower()
    if rpc("eth_chainId", []) != hex(chain_id):
        raise ValueError("Wrong wallet chain")
    block = rpc("eth_getBlockByNumber", [block_tag, False])
    if not isinstance(block, dict) or not isinstance(block.get("number"), str) or not QUANTITY.fullmatch(block["number"]):
        raise ValueError("Wallet block unavailable")
    height = block["number"]
    rows = []
    for asset, contract, decimals in assets:
        try:
            if contract is None:
                raw = rpc("eth_getBalance", [wallet, height])
                pattern = QUANTITY
            else:
                raw = rpc("eth_call", [{"to": contract, "data": "0x70a08231" + wallet[2:].zfill(64)}, height])
                pattern = BALANCE_WORD
            if not isinstance(raw, str) or not pattern.fullmatch(raw):
                raise ValueError("Invalid Ethereum balance response")
            units = format(as_units(int(raw, 16), decimals), "f")
        except (OSError, TypeError, ValueError):
            units = None
        rows.append({"asset": asset, "units": units})
    if all(row["units"] is None for row in rows):
        return {"status": "unavailable"}
    return {"status": "ready", "block": str(int(height, 16)), "balances": rows,
            "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def bounded_history(get, wallet, start, end, page_size=100, max_pages=3):
    """Get Etherscan V2 JSON via a host-supplied GET with its API key scoped there."""
    wallet = wallet.lower()
    if not ADDR.fullmatch(wallet) or not 0 <= start <= end:
        raise ValueError("Invalid Ethereum history scope")
    if not 1 <= page_size <= 100 or not 1 <= max_pages <= 10:
        raise ValueError("Invalid Ethereum page bounds")
    evidence = {}
    coverage = {}
    for action in ("txlist", "tokentx"):
        rows = []
        successful_pages = 0
        failed_pages = []
        end_of_results = False
        for page in range(1, max_pages + 1):
            try:
                data = get({"chainid": 1, "module": "account", "action": action,
                            "address": wallet, "startblock": start, "endblock": end,
                            "page": page, "offset": page_size, "sort": "asc"})
                values = data["result"]
                no_rows = data.get("status") == "0" and data.get("message") == "No transactions found" and values == []
                if not no_rows and (data.get("status") != "1" or not isinstance(values, list)):
                    raise ValueError("Unavailable")
                if len(values) > page_size or any(
                    not TX_HASH.fullmatch(row["hash"].lower()) or
                    not start <= int(row["blockNumber"]) <= end or
                    wallet not in ((row.get("from") or "").lower(), (row.get("to") or "").lower())
                    for row in values
                ):
                    raise ValueError("Outside scope")
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                # A failed page cannot establish coverage. Keep only earlier verified
                # pages and continue the independent stream.
                failed_pages.append(page)
                break
            rows.extend(values)
            successful_pages += 1
            if len(values) < page_size:
                end_of_results = True
                break
        evidence[action] = rows
        coverage[action] = {"rows": len(rows), "pages": successful_pages,
                            "failed_pages": failed_pages, "end_of_results": end_of_results}
    return evidence, {"start_block": start, "end_block": end, "streams": coverage,
                      "internal_transfers": "not fetched", "chain_id": 1}


def factory_pool(rpc, token0, token1, fee, block):
    if (token0, token1) != tuple(sorted(TOKENS)) or fee not in {100, 500, 3000, 10000}:
        raise ValueError("Unsupported pool")
    encoded = "0x1698ee82" + token0[2:].zfill(64) + token1[2:].zfill(64) + f"{fee:064x}"
    response = rpc("eth_call", [{"to": FACTORY, "data": encoded}, block])
    if not re.fullmatch(r"0x0{24}[a-f0-9]{40}", response):
        raise ValueError("Invalid factory response")
    return "0x" + response[-40:]


def abi_int(word):
    raw = int(word, 16)
    return raw - (1 << 256) if raw >= 1 << 255 else raw


def classify_swap(rpc, wallet, tx, receipt, unix_time, fee=500):
    """Return exact token legs only for a verified, directly funded single hop."""
    wallet = wallet.lower()
    txid = tx["hash"].lower()
    if (not ADDR.fullmatch(wallet) or not TX_HASH.fullmatch(txid) or
        tx["from"].lower() != wallet or (tx.get("to") or "").lower() not in ROUTERS or
        int(tx["value"], 16) != 0 or receipt["transactionHash"].lower() != txid or
        receipt.get("status") != "0x1" or tx["blockHash"] != receipt["blockHash"] or
        tx["blockNumber"] != receipt["blockNumber"]):
        return ()
    logs = receipt["logs"]
    swaps = [log for log in logs if log["topics"][:1] == [SWAP]]
    if len(swaps) != 1:
        return ()
    swap = swaps[0]
    token0, token1 = sorted(TOKENS)
    pool = factory_pool(rpc, token0, token1, fee, receipt["blockNumber"])
    if (swap["address"].lower() != pool or int(pool, 16) == 0 or
        not re.fullmatch(r"0x[0-9a-f]{320}", swap["data"])):
        return ()
    words = swap["data"][2:]
    deltas = {token0: abi_int(words[:64]), token1: abi_int(words[64:128])}
    if deltas[token0] * deltas[token1] >= 0:
        return ()
    matched = {}
    for log in logs:
        token = log["address"].lower()
        topics = log["topics"]
        if token not in deltas or topics[:1] != [TRANSFER] or len(topics) != 3:
            continue
        sender, receiver = ("0x" + topic[-40:] for topic in topics[1:])
        if wallet not in (sender, receiver):
            continue
        delta = deltas[token]
        expected = (wallet, pool) if delta > 0 else (pool, wallet)
        if (token in matched or (sender, receiver) != expected or
            not re.fullmatch(r"0x[0-9a-f]{64}", log["data"]) or
            int(log["data"], 16) != abs(delta)):
            return ()
        matched[token] = log["logIndex"]
    if matched.keys() != deltas.keys():
        return ()
    at = datetime.fromtimestamp(unix_time, timezone.utc).isoformat()
    group = f"eip155:1:{txid}:{swap['logIndex']}"
    return tuple(
        Entry(f"{group}:{token}", at, "defi_swap", f"eip155:1/erc20:{token}",
              as_units(-delta, TOKENS[token]),
              (f"{group}:swap", f"eip155:1:{txid}:{matched[token]}:transfer",
               f"factory:{FACTORY}:{receipt['blockNumber']}:{pool}"),
              related=group, original=(("pool_delta", str(delta)), ("pool", pool)))
        for token, delta in deltas.items()
    )


def inspect_swap(chain_rpc, factory_rpc, wallet, txid):
    """Verify chain, transaction, receipt and block before decoding the public swap."""
    wallet, txid = wallet.lower(), txid.lower()
    if not ADDR.fullmatch(wallet) or not TX_HASH.fullmatch(txid):
        raise ValueError("Use an Ethereum address and transaction hash")
    result = {"chain_id": 1, "wallet": wallet, "transaction": txid, "rows": []}
    try:
        if chain_rpc("eth_chainId", []) != "0x1" or factory_rpc("eth_chainId", []) != "0x1":
            raise ValueError("Provider returned a different chain")
        tx = chain_rpc("eth_getTransactionByHash", [txid])
        receipt = chain_rpc("eth_getTransactionReceipt", [txid])
        block = chain_rpc("eth_getBlockByNumber", [tx["blockNumber"], False])
        if (tx["hash"].lower() != txid or receipt["transactionHash"].lower() != txid or
            block["hash"] != tx["blockHash"] or block["hash"] != receipt["blockHash"] or
            block["number"] != tx["blockNumber"] or block["number"] != receipt["blockNumber"]):
            raise ValueError("Provider evidence did not agree")
        result["block"] = int(block["number"], 16)
        legs = classify_swap(factory_rpc, wallet, tx, receipt, int(block["timestamp"], 16))
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError):
        return {**result, "status": "unavailable", "reason": "Chain, receipt, block or factory evidence could not be verified."}
    if not legs:
        return {**result, "status": "unsupported", "reason": "No verified direct wallet-funded USDC/WETH Uniswap v3 swap at the supported fee tier."}
    return {**result, "status": "verified", "rows": [
        {"id": leg.key, "at": leg.at, "kind": leg.kind, "asset": leg.asset,
         "quantity": format(leg.quantity, "f"), "evidence": list(leg.evidence)}
        for leg in legs
    ]}
