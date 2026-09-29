"""Daily digest and analysis ledger in bootstrap (synthetic)."""
import tempfile
import unittest
from pathlib import Path

from phctx.store import Store
from phctx.tools import ToolContext, Tools

AT = '2026-09-20T12:00:00-05:00'


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / 'store', 'synthetic')
        self.t = Tools(ToolContext(store=self.s, profile='full'))
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def rid(self):
        self.n += 1
        return f'SYNTHETIC-request-{self.n}'

    def analysis(self, text, payload, at=AT):
        receipt = self.t.call('context_search', {}).data['read_receipt']
        out = self.t.call('context_capture', {'request_id': self.rid(), 'kind': 'analysis', 'text': text,
                                               'occurred_at': at, 'payload': payload, 'read_receipts': [receipt]})
        self.assertFalse(out.is_error, out.data)
        return out.data['record_id']

    def test_bootstrap_carries_the_latest_digest_whole_and_every_analysis_as_one_line(self):
        self.analysis('SYNTHETIC long investigation ' * 40, {'summary': 'SYNTHETIC sleep dipped in July',
                                                            'revisit_when': 'after 2 more weeks'})
        old = self.analysis('SYNTHETIC digest one', {'type': 'digest', 'summary': 'SYNTHETIC day 1'},
                            '2026-09-21T07:00:00-05:00')
        new = self.analysis('SYNTHETIC digest two', {'type': 'digest', 'summary': 'SYNTHETIC day 2'},
                            '2026-09-22T07:00:00-05:00')
        b = self.t.call('context_bootstrap', {}).data
        self.assertEqual(b['last_digest']['id'], new)
        self.assertEqual([e['id'] for e in b['analysis_ledger']][:2], [new, old])
        long = b['analysis_ledger'][-1]
        self.assertEqual((long['summary'], long['revisit_when'], long['date']),
                         ('SYNTHETIC sleep dipped in July', 'after 2 more weeks', '2026-09-20'))
        self.assertNotIn('text', long)  # the ledger names analyses; the body is one context_read away
        self.assertNotIn(new, [r['id'] for r in b['recent']])

    def test_since_last_digest_counts_what_arrived_after_it(self):
        self.analysis('SYNTHETIC digest', {'type': 'digest', 'summary': 'SYNTHETIC day 1'})
        self.s.put_record(request_id=self.rid(), kind='event', text='SYNTHETIC breakfast', occurred_at=AT)
        self.s.register_source('synthetic:watch', 'SYNTHETIC watch')
        self.s.ingest_batch(request_id=self.rid(), source_id='synthetic:watch', deleted_ids=[], cursor='c',
                            samples=[dict(native_id='a', metric='SYNTHETIC.m', start_at='2026-09-20T01:00:00+00:00',
                                          end_at='2026-09-20T01:00:00+00:00', value_num=1.0)])
        since = self.t.call('context_bootstrap', {}).data['since_last_digest']
        self.assertEqual(since['new_records'], {'event': 1})
        self.assertEqual(since['observation_batches']['synthetic:watch']['metrics'], ['SYNTHETIC.m'])

    def test_a_digest_read_through_bootstrap_is_delivered_evidence(self):
        digest = self.analysis('SYNTHETIC digest', {'type': 'digest', 'summary': 'SYNTHETIC day 1'})
        receipt = self.t.call('context_bootstrap', {}).data['read_receipt']
        with self.s.connect() as c:
            refs = c.execute('SELECT refs_json FROM read_receipts WHERE id=?', (receipt,)).fetchone()[0]
        self.assertIn(digest, refs)


if __name__ == '__main__':
    unittest.main()
