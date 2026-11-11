"""Build a deterministic, fictional snapshot from the actual ledger adapters."""
import hashlib
import json
from pathlib import Path

from ethereum import classify_swap
from kraken import normalize, read_export
from ledger import Entry, Ledger, number
from tests.fixtures import ACCOUNT, MOVEMENT, POOL, TRADE, WALLET, example, topic

SNAPSHOT = Path(__file__).resolve().parent / "dashboard" / "sample.json"


def transfer_examples():
    asset = "eip155:1/slip44:60"
    known_hash = "0x" + "a" * 64
    rows = []
    for index, (mode, kind, quantity, fee, at, tx) in enumerate((
        ("kraken-spot", "withdrawal", "-0.500", "0.010", "09:00", known_hash),
        ("ethereum", "transfer", "0.490", "0", "09:08", known_hash),
        ("kraken-spot", "withdrawal", "-0.125", "0", "13:00", ""),
        ("ethereum", "transfer", "0.125", "0", "13:32", ""),
    ), 1):
        ref = f"fictional-transfer-{index}"
        rows.append({"id": ref, "kind": kind, "at": f"2026-01-01T{at}:00+00:00",
                     "sourceMode": mode, "asset": asset, "quantity": quantity, "cost": "0",
                     "fee": fee, "fee_asset": asset if fee != "0" else "",
                     "transfer": {"asset": asset, "ref": ref, "account": f"fictional-{mode}",
                                  "hash": tx, "feeAsset": asset if fee != "0" else ""},
                     "evidence": [f"sample:{ref}"],
                     "observations": [{"fixture": "Fictional transfer record", "reference": ref,
                                       "amount": quantity, "hash": tx}]})
    return rows


def conflict_example():
    ledger = Ledger()
    identity = "L-FICTIONAL-CONFLICT"
    for source, timestamp, amount, fee in (
        ("api", "1767225600.123456", "0.0199", "0.0003"),
        ("csv", "2026-01-01 00:00:00", "0.02", "0.0002"),
    ):
        ledger.add(normalize(ACCOUNT, "ledger", identity, {
            "txid": identity, "refid": "T-FICTIONAL-CONFLICT", "time": timestamp,
            "type": "trade", "asset": "XXBT", "amount": amount, "fee": fee,
        }, source))
    return ledger.conflict_evidence(ledger.conflicts())


def history_examples():
    day = "2026-01-01"
    complete = {"pages": 1, "failed_pages": [], "complete": True}
    return {
        "kraken-spot": {"status": "ready", "start": day, "end": day,
                        "coverage": [{"section": name, **complete} for name in ("trades", "ledger")]},
        "binance-spot": {"status": "ready", "start": day, "end": day, "symbol": "ETHUSDT",
                         "coverage": [{"section": "spot-fills", **complete}]},
        "ethereum": {"status": "ready", "start": 16, "end": 18,
                     "coverage": [{"section": "txlist", "pages": 1, "failed_pages": [2],
                                   "complete": False, "gap": "failed-page"},
                                  {"section": "tokentx", **complete}]},
        "bybit-spot": {"status": "unavailable"},
        "bybit-csv": {"status": "empty"},
    }


PORTFOLIO = {'at': '2026-11-08T17:20:00Z',
 'positions': [{'name': 'Wallet tokens',
                'supplied': 100250,
                'assets': [['Ether', '16.00 ETH', 51200],
                           ['Wrapped bitcoin', '0.50 WBTC', 34000],
                           ['USD Coin', '12,650 USDC', 12650],
                           ['Other wallet tokens', '', 2400]]},
               {'name': 'Aave v3 on Ethereum', 'supplied': 35600, 'debt': 12000, 'health': '1.86'},
               {'name': 'Uniswap v3 on Base', 'supplied': 8400},
               {'name': 'Kraken', 'supplied': 32000}],
 'allocation': [['ETH', 76800], ['WBTC', 34000], ['USDC', 22650], ['Other / unclassified', 42800]]}


def build_snapshot():
    ledger = Ledger()
    for section, csv in (("trades", TRADE), ("ledger", MOVEMENT)):
        for row in read_export(csv, section, ACCOUNT):
            ledger.add(row)
    tx, receipt = example()
    for row in classify_swap(lambda *_: topic(POOL), WALLET, tx, receipt, 1767225600):
        ledger.add(row)
    ledger.add(Entry("sample:ethereum:eth-transfer-1", "2026-01-01T08:30:00+00:00",
                     "transfer", "eip155:1/slip44:60", number("-0.1"),
                     ("sample:ethereum:eth-transfer-1:transaction",),
                     original=(("fixture", "Fictional native transfer"),)))
    rows = []
    for row in ledger.entries():
        rows.append({
            "id": row.key, "at": row.at, "kind": row.kind, "asset": row.asset,
            "quantity": str(row.quantity), "cost": str(row.cost),
            "fee": str(row.fee), "fee_asset": row.fee_asset,
            "evidence": row.evidence,
            "observations": [dict(item.original) for item in ledger.observations(row.key)],
        })
    return {"mode": "fictional", "portfolio": PORTFOLIO, "rows": rows, "transfer_demo": transfer_examples(),
            "conflict_demo": conflict_example(), "history_demo": history_examples(),
            "sample_import": {"sha256": hashlib.sha256(TRADE.encode()).hexdigest(),
                              "bytes": len(TRADE.encode())},
            "coverage": ["Kraken sample: one spot fill and one linked ledger movement.",
                         "Ethereum sample: one native transfer and one single-pool swap.",
                         "Fictional coverage only. No real accounts, chain history or cost basis."],
            "conflicts": ledger.conflicts()}


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(build_snapshot(), indent=2) + "\n", encoding="utf-8")
