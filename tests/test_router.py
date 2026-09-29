"""Router backend and background context packet, with the transport faked: no network, no Keychain."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phctx import model, worker
from phctx.config import Config
from phctx.store import Store
from phctx.tools import ToolContext, Tools

AT = '2026-09-20T12:00:00-05:00'


def reply(content=None, calls=None, tokens=10):
    msg = {'role': 'assistant', 'content': content}
    if calls:
        msg['tool_calls'] = [{'id': f'c{i}', 'type': 'function',
                              'function': {'name': n, 'arguments': a if isinstance(a, str) else json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {'choices': [{'message': msg}], 'usage': {'prompt_tokens': tokens, 'completion_tokens': 1,
                                                     'total_tokens': tokens + 1}}


class Router(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'data', 'synthetic')
        self.tools = Tools(ToolContext(store=self.store, profile='readonly'))
        self.sent = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, replies, **kw):
        it = iter(replies)

        def post(body, key, timeout):
            self.sent.append(json.loads(json.dumps(body)))
            return next(it)
        with patch.object(model, '_post', post), patch.object(model, 'keychain_get', return_value='k'):
            return model.run_router('sys', 'task', model_id='m', reasoning_effort='low', tools=self.tools,
                                    parse=model.parse_candidate, **kw)

    def test_tool_loop_calls_readonly_tools_and_records_the_trace(self):
        cand, trace = self.run_with([reply(calls=[('context_bootstrap', {})]),
                                     reply('{"decision": "silence", "question_id": null, "topic": "x"}')])
        self.assertEqual(cand['decision'], 'silence')
        tool = [t for t in trace if t.get('tool')]
        self.assertEqual([t['tool'] for t in tool], ['context_bootstrap'])
        self.assertIn('read_receipt', tool[0]['result_text'])
        self.assertEqual(sum(t['prompt_tokens'] for t in trace if 'turn' in t), 20)
        self.assertEqual({t['function']['name'] for t in self.sent[0]['tools']} & {'context_capture'}, set())

    def test_writer_is_refused_by_the_profile_even_if_the_model_names_it(self):
        _, trace = self.run_with([reply(calls=[('context_capture', {'request_id': 'r', 'kind': 'note', 'text': 't',
                                                                    'occurred_at': AT})]),
                                  reply('{"decision": "silence"}')])
        self.assertIn('write_disabled_in_profile', trace[1]['result_text'])
        self.assertEqual(self.store.search()['total_matches'], 0)

    def test_results_are_truncated_with_a_marker(self):
        _, trace = self.run_with([reply(calls=[('context_bootstrap', {})]), reply('{"decision": "silence"}')],
                                 result_cap=100)
        self.assertIn('[truncated: 100 of', trace[1]['result_text'])

    def test_turn_budget_forces_a_final_answer_with_tools_off(self):
        self.run_with([reply(calls=[('context_bootstrap', {})]), reply('{"decision": "silence"}')], max_turns=1)
        self.assertEqual(self.sent[1]['tool_choice'], 'none')

    def test_invalid_json_gets_one_repair_turn_then_fails(self):
        cand, _ = self.run_with([reply('not json'), reply('```json\n{"decision": "silence"}\n```')])
        self.assertEqual(cand, {'decision': 'silence'})
        with self.assertRaises(model.ModelError) as e:
            self.run_with([reply('nope'), reply('{"decision": "surface"}')])
        self.assertEqual(e.exception.code, 'candidate_schema_invalid')

    def test_a_read_receipt_is_not_evidence_and_earns_the_repair_turn(self):
        rid = self.store.put_record(request_id='SYNTHETIC-r', kind='note', text='SYNTHETIC note',
                                    occurred_at=AT)['record_id']
        bad = {'decision': 'surface', 'topic': 't', 'why_now': 'w', 'what_changed': 'c', 'unknowns': 'u',
               'next_step': 'n', 'evidence_ids': ['rr_' + 'a' * 32], 'source_policies': ['durable']}
        unseen = dict(bad, evidence_ids=['rec_' + 'a' * 32])
        good = dict(bad, evidence_ids=[rid])
        read = reply(calls=[('context_read', {'record_ids': [rid]})])
        cand, _ = self.run_with([read, reply(json.dumps(bad)), reply(json.dumps(good))])
        self.assertEqual(cand['evidence_ids'], [rid])
        self.assertIn('rr_' + 'a' * 32, self.sent[2]['messages'][-1]['content'])
        with self.assertRaises(model.ModelError) as e:  # well-formed but never returned by a tool: retyped or recalled
            self.run_with([read, reply(json.dumps(unseen)), reply(json.dumps(unseen))])
        self.assertEqual(e.exception.code, 'candidate_evidence_invalid')

    def test_missing_key_never_calls_the_router(self):
        with patch.object(model, 'keychain_get', return_value=None), patch.object(model, '_post') as post:
            with self.assertRaises(model.ModelError) as e:
                model.run_router('s', 't', model_id='m', reasoning_effort='low')
        self.assertEqual(e.exception.code, 'router_key_missing')
        post.assert_not_called()

    def test_router_url_is_loopback(self):
        self.assertTrue(model.ROUTER_URL.startswith('http://127.0.0.1:'))


class Packet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'data', 'synthetic')
        self.cfg = Config(profile='synthetic', root=self.store.root, minimum_semantic_interval_seconds=0)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def rec(self, kind, text, payload=None, evidence=None):
        self.n += 1
        return self.store.put_record(request_id=f'SYNTHETIC-{self.n}', kind=kind, text=text, occurred_at=AT,
                                     payload=payload, evidence_ids=evidence)['record_id']

    def job(self):
        with self.store.connect() as c:
            return dict(c.execute("SELECT * FROM jobs WHERE type='revisit'").fetchone())

    def test_packet_carries_question_history_surfaced_items_and_the_trigger(self):
        q = self.rec('question', 'SYNTHETIC posture question', {'state': 'open', 'watch_terms': ['posture']})
        a = self.rec('analysis', 'SYNTHETIC baseline analysis', {'question_id': q, 'revisit_when': 'reassessment'}, [q])
        with self.store.transaction() as c:
            c.execute("INSERT INTO meta VALUES('worker_watermark', (SELECT max(seq) FROM changes)) "
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value')
        new = self.rec('note', 'SYNTHETIC posture reassessment')
        r = self.store.queue_insight(request_id='SYNTHETIC-i', candidate={
            'decision': 'surface', 'question_id': q, 'topic': 'SYNTHETIC topic', 'why_now': 'w', 'what_changed': 'c',
            'unknowns': 'u', 'next_step': 'n', 'evidence_ids': [new], 'source_policies': ['durable']})
        self.store.ack_insight(request_id='SYNTHETIC-a', insight_id=r['insight_id'])
        worker.plan(self.store, self.cfg)
        text = worker.packet(self.store, self.job())
        for part in (q, a, 'reassessment', r['insight_id'], 'delivered (shown to the user)', new,
                     'already_cited_by', f'set question_id to {q}'):
            self.assertIn(part, text)
        self.assertLess(len(text), worker.PACKET_CHARS)

    def test_observation_windows_are_disjoint_and_place_new_values_in_the_old_range(self):
        self.store.register_source('synthetic:watch', 'Synthetic Watch')

        def batch(days, value):
            self.n += 1
            self.store.ingest_batch(request_id=f'SYNTHETIC-{self.n}', source_id='synthetic:watch', deleted_ids=[],
                                    cursor=str(self.n), samples=[
                {'native_id': f'n{d}', 'metric': 'RestingHeartRate', 'start_at': f'2026-09-{d:02d}T07:00:00-05:00',
                 'end_at': f'2026-09-{d:02d}T07:30:00-05:00', 'value_num': value(d), 'unit': 'count/min'}
                for d in days])
        batch(range(1, 11), lambda d: 56 + d % 3)
        self.rec('question', 'SYNTHETIC resting heart rate', {'state': 'open', 'watch_metrics': ['RestingHeartRate']})
        with self.store.transaction() as c:
            c.execute("INSERT INTO meta VALUES('worker_watermark', (SELECT max(seq) FROM changes)) "
                      'ON CONFLICT(key) DO UPDATE SET value=excluded.value')
        batch(range(11, 14), lambda d: 63)
        worker.plan(self.store, self.cfg)
        text = worker.packet(self.store, self.job())
        m = json.loads(text[text.index('{"change":"observations"'):].splitlines()[0])['metrics'][0]
        self.assertEqual((m['previous_28_days_same_source']['n'], m['previous_28_days_same_source']['max']), (10, 58))
        self.assertEqual(m['new_values_vs_previous_range']['above_previous_max'], 3)

    def test_upcoming_lists_dated_user_events_not_analysis_revisit_times(self):
        from datetime import datetime
        def put(kind, text, at):
            self.n += 1
            self.store.put_record(request_id=f'SYNTHETIC-{self.n}', kind=kind, text=text, occurred_at=at)
        put('note', 'SYNTHETIC doctor follow-up on 2026-10-02 to decide on treatment.', AT)
        put('note', 'SYNTHETIC: checkpoint is next week.', '2026-09-22T09:00:00-05:00')
        put('routine', 'SYNTHETIC goal over 12 weeks (started 2026-07-01).', '2026-07-01T09:00:00-05:00')
        put('analysis', 'SYNTHETIC analysis: revisit on 2026-09-30.', AT)
        put('note', 'SYNTHETIC visit on 2026-12-24.', AT)  # beyond the window
        put('note', 'SYNTHETIC appointment on 2026-02-31.', AT)  # not a date
        with self.store.connect() as c:
            got = worker.upcoming(c, datetime.fromisoformat('2026-09-23T09:00:00-05:00'))
        self.assertEqual([(e['date'], e['days_until'], e['read_as']) for e in got],
                         [('2026-09-23', 0, 'weeks from the stated start'), ('2026-09-29', 6, 'relative to the record date'),
                          ('2026-10-02', 9, 'explicit date')])

    def test_oversized_sections_say_what_was_left_out(self):
        text = worker._section('T', [{'x': 'y' * 50}] * 10, 120, 'none')
        self.assertIn('8 more omitted', text)
        self.assertEqual(worker._section('T', [], 10, 'nothing'), '## T\nnothing')


if __name__ == '__main__':
    unittest.main()
