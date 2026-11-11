"""HOLD-02: bounded malicious request bodies must be handled, not HTTP 500."""
import json
from tests.security import harness

transfers = harness.mod("transfers")
ROUTES = ["/kraken/reconcile", "/binance/review", "/bybit/import", "/transfers/decisions",
          "/conflicts/decisions", "/performance/cost-basis"]
DEEP = b"[" * 100000 + b"]" * 100000


class InvalidBodyTests(harness.SecurityTest):
    def test_deep_json_returns_invalid_on_all_affected_routes(self):
        bad = []
        with harness.client() as client:
            for route in ROUTES:
                try:
                    response = client.post(route, content=DEEP, headers={"content-type": "application/json"})
                    if response.status_code != 200 or response.json() != {"status": "invalid"}:
                        bad.append((route, response.status_code, response.json()))
                except (RecursionError, ValueError, TypeError, AttributeError) as error:
                    bad.append((route, type(error).__name__))
        self.assertEqual(bad, [])

    def test_transfer_row_with_non_string_timestamp_is_handled(self):
        with harness.client() as client:
            for at in (None, 1, [], {}):
                with self.subTest(at=at):
                    rows = [dict(row) for row in transfers.fixture_rows()[:8]]
                    rows[0]["at"] = at
                    response = client.post("/transfers/decisions", content=json.dumps({
                        "mode": "combined-sample", "action": "list", "rows": rows}).encode(),
                        headers={"content-type": "application/json"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"status": "blocked"})

    def test_bad_timestamp_is_a_value_error_at_the_fact_boundary(self):
        row = dict(transfers.fixture_rows()[0], at=None)
        with self.assertRaisesRegex(ValueError, "A transfer timestamp needs an offset"):
            transfers._fact(row)

    def test_parser_recursion_error_is_handled_on_all_affected_routes(self):
        from unittest.mock import patch
        with harness.client() as client:
            for route in ROUTES:
                with self.subTest(route=route):
                    with patch.object(harness.api().json, "loads", side_effect=RecursionError("synthetic depth")):
                        response = client.post(route, content=b"{}", headers={"content-type": "application/json"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"status": "invalid"})
