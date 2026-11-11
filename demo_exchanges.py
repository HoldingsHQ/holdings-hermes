"""Fictional Binance and Bybit review snapshots, built here from the bundled fixture files.

The demo combined review shows these rows, and Ask Holdings treats them as demo data because this
side built them. Nothing here contacts a provider. The Binance read uses the bundled API fixture.
"""
import copy
import json
from functools import lru_cache
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")
BINANCE_CSV = "binance-fictional-spot-trade-history.csv"
BINANCE_API = "binance-fictional-spot-api.json"
BYBIT_CSV = "bybit-fictional-spot-trade-history.csv"
SOURCES = {"binance-spot": "Binance Spot", "bybit-csv": "Bybit Spot CSV"}


@lru_cache(maxsize=1)
def _built():
    from . import binance, binance_review, bybit_review

    api = json.loads((FIXTURES / BINANCE_API).read_text(encoding="utf-8"))
    read_only = {name: False for name in binance.REQUIRED_DENIALS} | {"enableReading": True}

    def bundled(path, *_):
        return read_only if path.endswith("apiRestrictions") else api

    return {
        "binance-spot": binance_review.review({"csv": (FIXTURES / BINANCE_CSV).read_text(encoding="utf-8"), "offset": "+07:00"},
                                              "fictional", "fictional", bundled),
        "bybit-csv": bybit_review.csv_review({"csv": (FIXTURES / BYBIT_CSV).read_text(encoding="utf-8")}),
    }


def snapshots():
    """Fresh copies, so a caller cannot change the cached fixtures."""
    return copy.deepcopy(_built())


def rows():
    """(mode, row) for every fictional exchange row."""
    return [(mode, row) for mode, snapshot in _built().items() for row in snapshot["rows"]]
