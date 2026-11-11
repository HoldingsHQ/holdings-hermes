"""Documented Zerion response shapes with fictional wallets, tokens and values."""
import base64
import io
import sys
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

package = ModuleType("holdings_zerion_tests")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
from holdings_zerion_tests import zerion

WALLET = "0x" + "11" * 20
WALLET_2 = "0x" + "22" * 20
CONTRACT = "0x" + "33" * 20
RECEIPT = "0x" + "44" * 20
KEY = "fictional-zerion-key"


def token(record="synthetic-token", kind="wallet", contract=CONTRACT, chain="ethereum"):
    implementations = [{"chain_id": chain, "address": contract}]
    return {"type": "positions", "id": record, "relationships": {"chain": {"data": {"id": chain}}}, "attributes": {
        "position_type": kind, "quantity": {"int": "12345678901234567890123456789012345", "decimals": 18},
        "fungible_info": {"symbol": "TEST", "implementations": implementations, "flags": {"verified": True}},
        "price": Decimal("1.234567890123456789"), "protocol": "Fictional protocol",
        "receipt": {"fungible_info": {"implementations": [{"chain_id": chain, "address": RECEIPT}]}},
    }}


def collectible(*, preview=None, floor=None):
    return {"type": "nft_positions", "id": "synthetic-nft", "relationships": {"chain": {"data": {"id": "ethereum"}}},
            "attributes": {"price": 55, "nft_info": {"contract_address": CONTRACT, "token_id": "123",
                "content": {"preview": {"url": preview}}, "name": "Example collectible"},
                "collection_info": {"name": "Fictional collection"}},
            "included": [] if floor is None else [{"type": "nfts", "relationships": {"chain": {"data": {"id": "ethereum"}}},
                "attributes": {"contract_address": CONTRACT, "token_id": "123", "market_data": {"prices": {"floor": floor}}}}]}


class ZerionTests(unittest.TestCase):
    def test_https_basic_auth_and_statuses(self):
        response = io.BytesIO(b'{"result":0.01000000000000000001}')
        response.status = 200
        path = f"/v1/wallets/{WALLET}/positions/"
        with patch.object(zerion, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(zerion.get(path, {"filter[chain_ids]": "ethereum"}, KEY),
                             ("ready", {"result": Decimal("0.01000000000000000001")}))
            request = opener.return_value.open.call_args.args[0]
            url = urlsplit(request.full_url)
            self.assertEqual((url.scheme, url.netloc, url.path, request.get_method()), ("https", zerion.HOST, path, "GET"))
            self.assertEqual(parse_qs(url.query), {"filter[chain_ids]": ["ethereum"]})
            self.assertEqual(base64.b64decode(request.get_header("Authorization").split()[1]).decode(), KEY + ":")
            self.assertIsInstance(opener.call_args.args[0], zerion.NoRedirect)
            for code, status in [(302, "unavailable"), (401, "invalid-key"), (403, "access-denied"), (429, "rate-limited")]:
                opener.return_value.open.side_effect = HTTPError(request.full_url, code, "", {}, None)
                self.assertEqual(zerion.get(path, {}, KEY), (status, None))

    def test_response_above_byte_budget_is_reported_without_parsing(self):
        response = io.BytesIO(b"x" * 5)
        response.status = 200
        with patch.object(zerion, "MAX_BYTES", 4), patch.object(zerion, "build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(zerion.get(f"/v1/wallets/{WALLET}/positions/", {}, KEY), ("response-limit", None))

    def test_wallet_chains_pagination_and_only_selected_portfolio_reference(self):
        reads = []

        def fetch(path, params, key):
            self.assertEqual(key, KEY)
            reads.append(path)
            if path.endswith("/portfolio"):
                return "ready", {"data": {"type": "portfolio", "attributes": {"positions_distribution_by_chain": {
                    "ethereum": Decimal("2.00000000000000001"), "base": 22, "unselected": 999}}}}
            chain = params["filter[chain_ids]"]
            self.assertEqual(params["filter[positions]"], "only_simple")
            if chain == "ethereum" and "page[after]" not in params:
                return "ready", {"data": [token()], "links": {"next": f"https://{zerion.HOST}{path}?page%5Bafter%5D=opaque"}}
            return "ready", {"data": [token(chain=chain)], "links": {}}

        result = zerion.snapshot([(WALLET, 1), (WALLET_2, 8453)], "tokens", KEY, fetch)
        first, second = result["wallets"]
        self.assertEqual(first["rows"][0]["units"], "12345678901234567.890123456789012345")
        self.assertEqual([first["pages"], second["pages"]], [2, 1])
        self.assertEqual([first["reference"]["value"], second["reference"]["value"]], ["2.00000000000000001", "22"])
        self.assertEqual(len(first["rows"]), 1)
        self.assertEqual(len(reads), 5)

    def test_larger_provider_page_respects_total_budget_and_keeps_supported_rows(self):
        def fetch_rows(total):
            rows = [token(record=f"synthetic-{index}", kind="deposit") for index in range(total)]
            return lambda *_: ("ready", {"data": rows, "links": {}})
        exact = zerion.wallet_report(WALLET, "ethereum", "defi", KEY, fetch_rows(zerion.PAGE_SIZE + 1))
        self.assertEqual((exact["state"], exact["pages"], len(exact["rows"])),
                         ("complete", 1, zerion.PAGE_SIZE + 1))
        limited = zerion.wallet_report(WALLET, "ethereum", "defi", KEY, fetch_rows(zerion.MAX_ROWS + 1))
        self.assertEqual((limited["state"], limited["pages"], len(limited["rows"])),
                         ("row-limit", 1, zerion.MAX_ROWS))

    def test_complex_position_receipt_and_debt(self):
        borrowed = token(record="synthetic-loan", kind="loan")
        borrowed["attributes"]["quantity"]["int"] = "-12000"
        supplied = token(record="synthetic-supply", kind="deposit")
        supplied["attributes"]["protocol_module"] = "liquidity"
        def fetch(path, params, key):
            self.assertEqual(params["filter[positions]"], "only_complex")
            return "ready", {"data": [borrowed, supplied], "links": {}}
        report = zerion.snapshot([(WALLET, 1)], "defi", KEY, fetch)["wallets"][0]
        self.assertIsNone(report["reference"])
        self.assertEqual([(row["category"], row["receipt"]) for row in report["rows"]],
                         [("Borrowed", RECEIPT), ("Liquidity", RECEIPT)])
        self.assertEqual(report["rows"][0]["units"], "0.000000000000012000")

    def test_invalid_cursor_conflict_foreign_chain_and_rate_limit(self):
        invalid = deepcopy(token())
        invalid["attributes"]["quantity"]["int"] = "4"
        def fetch(path, params, key):
            return "ready", {"data": [token(), invalid, token(chain="base")],
                             "links": {"next": "https://foreign.invalid/positions/?page[after]=x"}}
        report = zerion.snapshot([(WALLET, 1)], "tokens", KEY, fetch)["wallets"][0]
        self.assertEqual((report["rows"], report["state"], report["skipped"]), ([], "incomplete", 2))
        reads = []
        def throttled(path, params, key):
            reads.append(path)
            return "rate-limited", None
        result = zerion.snapshot([(WALLET, 1), (WALLET_2, 8453)], "tokens", KEY, throttled)
        self.assertEqual((len(reads), result["wallets"][0]["state"]), (1, "rate-limited"))

    def test_nft_unpriced_and_approved_image_and_floor(self):
        no_price = zerion.nft(collectible(preview="https://foreign.invalid/a.png"), "ethereum")
        self.assertIsNone(no_price["image"])
        self.assertIsNone(no_price["floor"])
        url = "https://token-icons.s3.amazonaws.com/fictional.png"
        item = collectible(preview=url, floor=Decimal("0.000001"))
        row = zerion.nft(item, "ethereum")
        self.assertEqual((row["image"], row["floor"], row["tokenId"]), (url, "0.000001", "123"))
        self.assertIsNone(zerion.nft(collectible(floor=0), "ethereum")["floor"])
        item["included"][0]["attributes"]["token_id"] = "456"
        self.assertIsNone(zerion.nft(item, "ethereum")["floor"])

    def test_live_nft_shape_joins_related_floor_and_allows_fixed_thumbnail(self):
        image = "https://nft2-cdn.alchemy.com/example/image/width=200,format=png/base-mainnet/synthetic-media"
        item = collectible(preview=image, floor=None)
        item["relationships"]["nft"] = {"data": {"type": "nfts", "id": "opaque-related"}}
        related = collectible(floor=Decimal("0.001234567890123456789"))["included"][0]
        related["id"] = "opaque-related"
        related.pop("relationships")
        body = {"data": [item], "links": {}, "included": [related]}
        result = zerion.wallet_report(WALLET, "ethereum", "nfts", KEY, lambda *_: ("ready", body))
        row = result["rows"][0]
        self.assertEqual(row["floor"], "0.001234567890123456789")
        self.assertEqual(row["image"], image)

        duplicates = zerion.included_nfts({"included": [related, related]})
        self.assertIsNone(zerion.nft(item, "ethereum", duplicates)["floor"])
        wrong = deepcopy(related)
        wrong["attributes"]["token_id"] = "999"
        self.assertIsNone(zerion.nft(item, "ethereum", {"opaque-related": wrong})["floor"])
        item["attributes"]["nft_info"]["content"]["preview"]["url"] = image + "?redirect=external"
        self.assertIsNone(zerion.nft(item, "ethereum")["image"])

    def test_pnl_is_wallet_and_chain_scoped_with_exact_signed_values(self):
        requests = []
        def fetch(path, params, key):
            requests.append((path, params))
            return "ready", {"data": {"type": "wallet_pnl", "id": WALLET.upper(),
                "attributes": {"realized_gain": Decimal("-0.002310123456789"), "total_gain": 0}},
                "meta": {"excluded_fungible_ids": ["fictional-id"]}}
        report = zerion.snapshot([(WALLET, 8453)], "pnl", KEY, fetch)
        self.assertEqual(requests, [(f"/v1/wallets/{WALLET}/pnl", {"currency": "usd", "filter[chain_ids]": "base"})])
        self.assertEqual(report["wallets"][0], {"state": "complete", "pnl": {
            "realized_gain": "-0.002310123456789", "unrealized_gain": None, "total_gain": "0"}, "exclusions": True})
        self.assertEqual(zerion.wallet_pnl(WALLET_2, "base", KEY, fetch), {"state": "unavailable"})
        self.assertEqual(zerion.wallet_pnl(WALLET, "base", KEY, lambda *_: ("rate-limited", None)), {"state": "rate-limited"})

    def test_selected_pnl_totals_preserve_signed_values_and_missing_components(self):
        selection = [(WALLET, 8453), (WALLET_2, 1)]

        def fetch(path, params, key):
            wallet = path.split("/")[3]
            return "ready", {"data": {"type": "wallet_pnl", "id": wallet, "attributes": {
                "realized_gain": Decimal("3.123456789012345678901") if wallet == WALLET else Decimal("-3.000000000000000000001"),
                "unrealized_gain": 4 if wallet == WALLET else None,
                "total_gain": -2 if wallet == WALLET else 1}}}

        self.assertEqual(zerion.snapshot(selection, "pnl", KEY, fetch)["totals"], {
            "realized_gain": "0.123456789012345678900", "total_gain": "-1"})
        limited = zerion.snapshot(selection, "pnl", KEY, lambda *_: ("rate-limited", None))
        self.assertEqual(limited["totals"], {})

    def test_wallet_chart_validates_scope_order_and_exact_series(self):
        calls = []
        payload = {"data": {"type": "wallet_chart", "id": f"{WALLET}-week",
                            "attributes": {"points": [[1700000000, Decimal("5.000000000000001")], [1700001000, -2]]}}}
        def fetch(path, params, key):
            calls.append((path, params))
            return "ready", payload
        report = zerion.snapshot([(WALLET, 8453)], "chart-week", KEY, fetch)["wallets"][0]
        self.assertEqual(calls, [(f"/v1/wallets/{WALLET}/charts/week", {"currency": "usd", "filter[chain_ids]": "base"})])
        self.assertEqual(report, {"state": "complete", "points": [[1700000000, "5.000000000000001"], [1700001000, "-2"]]})
        self.assertEqual(zerion.wallet_chart(WALLET, "base", "month", KEY, fetch), {"state": "unavailable"})
        payload["data"]["attributes"]["points"].reverse()
        self.assertEqual(zerion.wallet_chart(WALLET, "base", "week", KEY, fetch), {"state": "unavailable"})
        payload["data"]["attributes"]["points"] = []
        self.assertEqual(zerion.wallet_chart(WALLET, "base", "week", KEY, fetch), {"state": "empty"})

    def test_day_chart_combines_only_matching_times_and_keeps_partial_reports(self):
        calls = []
        selection = [(WALLET, 8453), (WALLET_2, 1)]
        a, b, c, d = range(1700000000, 1700001200, 300)

        def fetch(path, params, key):
            calls.append((path, params))
            wallet = path.split("/")[3]
            points = [[a, 7], [b, Decimal("5.000000000000001")], [c, -2]] if wallet == WALLET else [[b, 1], [c, 3], [d, 9]]
            return "ready", {"data": {"type": "wallet_chart", "id": f"{wallet}-day", "attributes": {"points": points}}}

        result = zerion.snapshot(selection, "chart-day", KEY, fetch)
        self.assertEqual(calls, [(f"/v1/wallets/{wallet}/charts/day", {"currency": "usd", "filter[chain_ids]": chain})
                                 for wallet, chain in [(WALLET, "base"), (WALLET_2, "ethereum")]])
        self.assertEqual(result["combinedPoints"], [[b, "6.000000000000001"], [c, "1"]])

        def throttled(path, params, key):
            return fetch(path, params, key) if WALLET in path else ("rate-limited", None)

        partial = zerion.snapshot(selection, "chart-day", KEY, throttled)
        self.assertEqual([report["state"] for report in partial["wallets"]], ["complete", "rate-limited"])
        self.assertEqual(partial["combinedPoints"], [])
        self.assertEqual(zerion.combined_chart([{"state": "complete", "points": [[a, "1"], [b, "2"]]},
                                               {"state": "complete", "points": [[b, "3"], [c, "4"]]}], 2), [])
