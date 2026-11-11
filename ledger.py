"""Source observations and exact decimal ledger fields for Holdings."""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Expected an exact decimal")
    raw = str(value)
    if len(raw) > 160 or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d{1,3})?", raw):
        raise ValueError("Invalid decimal")
    try:
        result = Decimal(raw)
    except InvalidOperation:
        raise ValueError("Invalid decimal") from None
    if abs(result.as_tuple().exponent) > 100 or (result and abs(result.adjusted()) > 100):
        raise ValueError("Decimal out of bounds")
    return result


def as_units(value, decimals):
    with localcontext() as context:
        context.prec = 100
        return number(value).scaleb(-decimals)


@dataclass(frozen=True)
class Entry:
    key: str
    at: str
    kind: str
    asset: str
    quantity: Decimal
    evidence: tuple
    fee_asset: str = ""
    fee: Decimal = Decimal(0)
    related: str = ""
    cost: Decimal = Decimal(0)
    original: tuple = ()

    def facts(self):
        return (self.at, self.kind, self.asset, self.quantity,
                self.fee_asset, self.fee, self.related, self.cost)


class Ledger:
    """In-memory only. Retain conflicting observations and omit them from totals."""

    def __init__(self):
        self._observations = {}

    def add(self, entry):
        self._observations.setdefault(entry.key, set()).add(entry)

    def _groups(self, key):
        groups = {}
        for entry in self._observations[key]:
            groups.setdefault(entry.facts(), set()).update(entry.evidence)
        return groups

    def observations(self, key):
        return tuple(sorted(self._observations.get(key, ()), key=repr))

    def conflicts(self):
        return {key: len(self._groups(key)) for key in sorted(self._observations)
                if len(self._groups(key)) > 1}

    def conflict_evidence(self, conflicts):
        return {
            key: [{
                "originalId": row.key,
                "at": row.at,
                "kind": row.kind,
                "asset": row.asset,
                "quantity": format(row.quantity, "f"),
                "cost": format(row.cost, "f"),
                "fee": format(row.fee, "f"),
                "fee_asset": row.fee_asset,
                "evidence": row.evidence,
                "original": dict(row.original),
            } for row in self.observations(key)]
            for key in conflicts
        }

    def entries(self):
        for key in sorted(self._observations):
            groups = self._groups(key)
            if len(groups) == 1:
                facts, evidence = next(iter(groups.items()))
                at, kind, asset, qty, fee_asset, fee, related, cost = facts
                yield Entry(key, at, kind, asset, qty, tuple(sorted(evidence)),
                            fee_asset, fee, related, cost)
