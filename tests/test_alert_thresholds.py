"""Editor-number thresholds use decimal comparisons without widening ledger inputs."""
import importlib
import json
import unittest
from unittest.mock import patch
from tests.test_sample_chat import package

watch = importlib.import_module(f"{package.__name__}.alert_watch")


class AlertThresholdTests(unittest.TestCase):
    def setUp(self):
        self.balances = [{'chainId': 1, 'balances': [
            {'contract': watch.market.WETH, 'units': '1'}]}]

    def price(self, threshold, change):
        with patch.object(watch.market, 'pool_snapshot', return_value={
                'status': 'ready', 'stale': False, 'marketChanges': {'24h': change},
                'closedAt': '2026-11-10T00:00:00Z'}):
            return watch._price(self.balances, threshold)

    def test_fractional_price_inclusive_exact_boundary(self):
        for change, count in [('0.099999999999999999', 0), ('0.1', 1),
                              ('0.100000000000000001', 1), ('-0.1', 1)]:
            with self.subTest(change=change):
                state, items = self.price(0.1, change)
                self.assertEqual(state, 'ready')
                self.assertEqual(len(items), count)

    def test_integer_thresholds_keep_working(self):
        self.assertEqual(self.price(5, '4.999')[1], [])
        self.assertEqual(len(self.price(5, '5')[1]), 1)

    def test_editor_rules_survive_json_roundtrip_and_evaluate(self):
        state = watch.default_state(watch.MODE)
        for rule in state['rules'].values():
            rule['enabled'] = False
        key, rule = watch.rule_update({'rule': 'price', 'threshold': 0.1,
                                       'enabled': True, 'muted': False})
        state['rules'][key] = rule
        state = json.loads(json.dumps(state))
        with patch.object(watch, '_balance_check', return_value=self.balances), \
             patch.object(watch.market, 'pool_snapshot', return_value={
                 'status': 'ready', 'stale': False, 'marketChanges': {'24h': '0.1'},
                 'closedAt': '2026-11-10T00:00:00Z'}):
            checks, items = watch.evaluate(state, get_secret=lambda name: self.fail('Unexpected secret'))
        self.assertEqual(checks, {'price': 'ready'})
        self.assertEqual({item['rule'] for item in items}, {'price'})

    def test_invalid_editor_values_and_unavailable_feeds_stay_rejected(self):
        for threshold in (True, None, '0.5', float('nan'), float('inf'), -0.1, 0, 100.1):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                watch.rule_update({'rule': 'price', 'threshold': threshold, 'enabled': True, 'muted': False})
        with patch.object(watch.market, 'pool_snapshot', return_value={'status': 'unavailable'}):
            self.assertEqual(watch._price(self.balances, 0.1), ('unavailable', []))
        with self.assertRaises(ValueError):
            watch.number(0.1)  # Financial-record parsing remains strict.
