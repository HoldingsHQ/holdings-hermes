"""Fictional records with schema-compatible test values."""
from ethereum import ROUTERS, SWAP, TOKENS, TRANSFER

ACCOUNT = "sample"
TRADE = ("txid,ordertxid,pair,time,type,price,cost,fee,vol,ledgers\n"
         "T-SAMPLE,O-SAMPLE,XBT/USD,2026-01-01 00:00:00,buy,100,2,0.02,0.02,L-SAMPLE\n")
MOVEMENT = ("txid,refid,time,type,subtype,asset,amount,fee,balance\n"
            "L-SAMPLE,T-SAMPLE,2026-01-01 00:00:00,trade,,XXBT,0.02,0.0002,0.0198\n")
WALLET = "0x" + "11" * 20
# Fictional transaction and quantities, using the real supported pool identity.
POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"
HASH = "0x" + "33" * 32
BLOCK = "0x10"
BLOCK_HASH = "0x" + "44" * 32
TOKEN0, TOKEN1 = sorted(TOKENS)


def word(value):
    return f"{value % (1 << 256):064x}"


def topic(address):
    return "0x" + address[2:].zfill(64)


def transfer(token, sender, recipient, amount, index):
    return {"address": token, "topics": [TRANSFER, topic(sender), topic(recipient)],
            "data": "0x" + word(amount), "logIndex": index}


def example():
    tx = {"hash": HASH, "from": WALLET, "to": sorted(ROUTERS)[0],
          "value": "0x0", "blockHash": BLOCK_HASH, "blockNumber": BLOCK}
    receipt = {"transactionHash": HASH, "status": "0x1", "blockHash": BLOCK_HASH,
               "blockNumber": BLOCK, "logs": [
                   {"address": POOL, "topics": [SWAP], "logIndex": "0x2",
                    "data": "0x" + "".join(map(word, (1_000_000, -10**16, 1, 1, 0)))},
                   transfer(TOKEN0, WALLET, POOL, 1_000_000, "0x0"),
                   transfer(TOKEN1, POOL, WALLET, 10**16, "0x1"),
               ]}
    return tx, receipt
