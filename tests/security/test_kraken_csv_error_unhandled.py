"""HOLD-02: CSV parser failures normalize to the existing blocked response."""
import base64
from tests.security import harness

GOOD = "txid,refid,time,type,asset,amount,fee\n"


class KrakenCsvErrorTests(harness.SecurityTest):
    def setUp(self):
        super().setUp()
        self.secrets.update(HOLDINGS_KRAKEN_API_KEY="synthetic-key",
                            HOLDINGS_KRAKEN_API_SECRET=base64.b64encode(b"synthetic-secret").decode())

    def check_csv(self, trades):
        with harness.client() as client:
            response = client.post("/kraken/reconcile", json={"start": "2026-01-01", "end": "2026-01-02",
                                                           "trades": trades, "ledgers": GOOD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "data-review-blocked"})

    def test_malformed_quote_is_handled(self):
        self.check_csv('txid,time,pair,type,vol,cost,fee\n"a"b,1,2,3,4,5,6\n')

    def test_oversized_field_is_handled(self):
        self.check_csv('txid,time,pair,type,vol,cost,fee\n' + 'x' * 200000 + ',1,2,3,4,5,6\n')
