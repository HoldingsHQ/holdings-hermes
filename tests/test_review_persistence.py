"""Protected real-mode decisions with synthetic records survive reopening; missing custody stays closed."""
import hashlib
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.test_transfers import transfers, examples
storage = importlib.import_module(transfers.__package__ + '.storage_proof')

class ProtectedReviewPersistence(unittest.TestCase):
    def test_encrypted_live_mode_accept_reopen_and_undo(self):
        try:
            import cryptography
        except ImportError:
            self.skipTest('cryptography is required for the protected-storage integration')
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder); key=os.urandom(32); scope=hashlib.sha256(str(home).encode()).digest()
            with patch.object(storage, 'protected_context', return_value=(home,key,scope)):
                rows=examples(); base={'mode':'combined','rows':rows}
                item=transfers.review(dict(base,action='list'))['items'][0]
                pair=[r for r in rows if r['id'] in (item['outgoing'],item['incoming'])]
                result=transfers.review(dict(base,action='accept',rows=pair,id=item['id'],revision=0,owned=True))
                self.assertEqual(result['item']['status'],'accepted')
                recovered=transfers.review(dict(base,action='list'))
                self.assertFalse(recovered.get('sessionOnly',False))
                self.assertEqual(recovered['items'][0]['status'],'accepted')
                saved=list((home/'plugin-data'/'holdings').glob('*.enc'))
                self.assertTrue(saved)
                for path in saved:
                    self.assertNotIn(b'accepted',path.read_bytes())
                undone=transfers.review(dict(base,action='undo',rows=pair,id=item['id'],revision=1))
                self.assertEqual(undone['item']['status'],'undone')
                self.assertEqual(transfers.review(dict(base,action='list'))['items'][0]['status'],'undone')
    def test_missing_external_custody_cannot_write(self):
        with patch.object(storage,'protected_context',side_effect=storage.ProtectedKeyUnavailable('Unavailable')):
            with self.assertRaises(storage.ProtectedKeyUnavailable):
                transfers.review({'mode':'combined','action':'list','rows':examples()})
