"""Evidence-chain binding regressions (R2-11, R2-12, R2-13): run_manifest --input chains and argv path fencing,
test-report tree-hashing precision, summarize's cross-campaign row/prompt validation, and the structured
live-evidence probe. Synthetic only; nothing under the real delivery/ is read or written, only files under a
per-test temp dir (fixture style copied from tests/test_security_ops.py's Tmp/ReleaseBindingTests/SummarizeTests,
not imported from it)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {'src': 'a' * 64, 'prompts/foreground.md': 'p' * 64, 'prompts/background.md': 'q' * 64}
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'evals')]


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)


# ---- R2-11(a): run_manifest --input chains evidence to a still-current, clean prior manifest -------------------
class RunManifestInputChainTests(Tmp):
    def setUp(self):
        super().setUp()
        import run_manifest
        self.rm = run_manifest
        (self.base / 'eval-report' / 'dev_old').mkdir(parents=True)
        self.old_rel = 'eval-report/dev_old/results_all_runs.jsonl'
        cmd = [sys.executable, '-c', f'open({str(self.base / self.old_rel)!r}, "w").write("SYNTHETIC old campaign")']
        with patch('sys.stdout'):
            rc = self.rm.main(['oldcamp', '--evidence', self.old_rel, '--', *cmd], delivery=self.base)
        self.assertEqual(rc, 0)

    def run_with_hashes(self, name, inputs, hashes):
        argv = [name]
        for rel in inputs:
            argv += ['--input', rel]
        argv += ['--', sys.executable, '-c', 'pass']
        with patch.object(self.rm, 'tree_hashes', return_value=hashes), patch('sys.stdout'), patch('sys.stderr'):
            return self.rm.main(argv, delivery=self.base)

    def test_input_bound_to_current_tree_is_accepted(self):
        cur = self.rm.tree_hashes()
        rc = self.run_with_hashes('newcamp', [self.old_rel], cur)
        self.assertEqual(rc, 0)
        m = json.loads((self.base / 'run-manifests' / 'newcamp.json').read_text())
        self.assertEqual(m['inputs'][self.old_rel]['manifest'], 'oldcamp.json')
        self.assertEqual(m['inputs'][self.old_rel]['sha256'],
                         hashlib.sha256((self.base / self.old_rel).read_bytes()).hexdigest())

    def test_stale_input_is_refused(self):
        stale = dict(self.rm.tree_hashes(), src='0' * 64)
        rc = self.run_with_hashes('newcamp', [self.old_rel], stale)
        self.assertEqual(rc, 2)
        self.assertFalse((self.base / 'run-manifests' / 'newcamp.json').exists())

    def test_missing_input_file_is_refused(self):
        rc = self.run_with_hashes('newcamp', ['eval-report/does_not_exist.jsonl'], self.rm.tree_hashes())
        self.assertEqual(rc, 2)
        self.assertFalse((self.base / 'run-manifests' / 'newcamp.json').exists())

    def test_input_from_a_dirty_or_failed_run_is_refused(self):
        (self.base / 'eval-report' / 'dev_dirty').mkdir()
        dirty_rel = 'eval-report/dev_dirty/results_all_runs.jsonl'
        cmd = [sys.executable, '-c', f'open({str(self.base / dirty_rel)!r}, "w").write("SYNTHETIC dirty")']
        with patch('sys.stdout'):
            rc = self.rm.main(['dirtycamp', '--evidence', dirty_rel, '--', *cmd], delivery=self.base)
        self.assertEqual(rc, 0)
        m = self.base / 'run-manifests' / 'dirtycamp.json'
        rec = json.loads(m.read_text())
        rec['tree_changed_during_run'] = True
        m.write_text(json.dumps(rec))
        rc = self.run_with_hashes('newcamp', [dirty_rel], self.rm.tree_hashes())
        self.assertEqual(rc, 2)
        self.assertFalse((self.base / 'run-manifests' / 'newcamp.json').exists())


# ---- R2-11(b): a command whose argv points outside the repo root is refused ------------------------------------
class ArgvPathFenceTests(Tmp):
    def setUp(self):
        super().setUp()
        import run_manifest
        self.rm = run_manifest

    def test_absolute_path_outside_repo_is_refused(self):
        outside = self.base / 'wrapper_script.sh'
        outside.write_text('#!/bin/bash\necho SYNTHETIC\n')
        with patch('sys.stdout'), patch('sys.stderr'):
            rc = self.rm.main(['x', '--', '/bin/bash', str(outside)], delivery=self.base)
        self.assertEqual(rc, 2)
        self.assertFalse((self.base / 'run-manifests').exists())

    def test_interpreter_and_bash_and_env_are_allowed(self):
        with patch('sys.stdout'):
            rc = self.rm.main(['x', '--', sys.executable, '-c', 'pass'], delivery=self.base)
        self.assertEqual(rc, 0)

    def test_path_under_the_repo_venv_is_allowed(self):
        venv_py = ROOT / '.venv' / 'bin' / 'python3'
        if not venv_py.is_file():
            self.skipTest('.venv/bin/python3 not present')
        with patch('sys.stdout'):
            rc = self.rm.main(['x', '--', str(venv_py), '-c', 'pass'], delivery=self.base)
        self.assertEqual(rc, 0)

    def test_path_under_the_repo_root_is_allowed(self):
        # an existing repo-root file named in argv (here: an inert extra argument the "pass" script ignores)
        # must not itself trigger the outside-repo refusal.
        with patch('sys.stdout'):
            rc = self.rm.main(['x', '--', sys.executable, '-c', 'pass', str(ROOT / 'pyproject.toml')],
                              delivery=self.base)
        self.assertEqual(rc, 0)


# ---- R2-11(c): tree hashing skips only .log/.json evidence under test-report, not executable drivers -----------
class TestReportSkipTests(Tmp):
    def setUp(self):
        super().setUp()
        import run_manifest
        self.rm = run_manifest
        p = patch.object(self.rm, 'ROOT', self.base)
        p.start()
        self.addCleanup(p.stop)
        (self.base / 'ios' / 'test-report').mkdir(parents=True)
        (self.base / 'ios' / 'src.swift').write_text('SYNTHETIC swift source')

    def test_log_under_test_report_does_not_change_the_hash(self):
        before = self.rm.tree_hash(self.base / 'ios')
        (self.base / 'ios' / 'test-report' / 'run.log').write_text('SYNTHETIC log output')
        self.assertEqual(before, self.rm.tree_hash(self.base / 'ios'))

    def test_json_under_test_report_does_not_change_the_hash(self):
        before = self.rm.tree_hash(self.base / 'ios')
        (self.base / 'ios' / 'test-report' / 'result.json').write_text('{}')
        self.assertEqual(before, self.rm.tree_hash(self.base / 'ios'))

    def test_executable_driver_under_test_report_changes_the_hash(self):
        before = self.rm.tree_hash(self.base / 'ios')
        (self.base / 'ios' / 'test-report' / 'run_tests.sh').write_text('#!/bin/bash\necho SYNTHETIC\n')
        self.assertNotEqual(before, self.rm.tree_hash(self.base / 'ios'))

    def test_pycache_is_still_fully_skipped(self):
        before = self.rm.tree_hash(self.base / 'ios')
        d = self.base / 'ios' / '__pycache__'
        d.mkdir()
        (d / 'x.pyc').write_bytes(b'SYNTHETIC bytecode')
        self.assertEqual(before, self.rm.tree_hash(self.base / 'ios'))


# ---- R2-11(a)+build_release: a manifest's declared inputs must themselves still be validly bound ---------------
class ReleaseInputChainTests(Tmp):
    def setUp(self):
        super().setUp()
        import build_release
        import run_manifest
        self.br, self.rm = build_release, run_manifest
        p = patch.object(self.br, 'D', self.base)
        p.start()
        self.addCleanup(p.stop)
        (self.base / 'run-manifests').mkdir()
        self.cur = self.rm.tree_hashes()
        self.old_rel = 'eval-report/dev_old/results_all_runs.jsonl'
        (self.base / 'eval-report' / 'dev_old').mkdir(parents=True)
        (self.base / self.old_rel).write_text('SYNTHETIC old campaign results')
        self.old_digest = hashlib.sha256((self.base / self.old_rel).read_bytes()).hexdigest()
        self.summary_rel = 'eval-report/summary.json'
        (self.base / self.summary_rel).write_text('SYNTHETIC summary')
        self.summary_digest = hashlib.sha256((self.base / self.summary_rel).read_bytes()).hexdigest()

    def write_manifest(self, name, hashes, evidence=None, inputs=None, exit_code=0, tree_changed=False):
        path = self.base / 'run-manifests' / f'{name}.json'
        path.write_text(json.dumps({
            'name': name, 'hashes': hashes, 'exit_code': exit_code, 'tree_changed_during_run': tree_changed,
            'evidence': evidence or {}, 'inputs': inputs or {}}))
        return path

    def make_summary_manifest(self, old_hashes):
        old_path = self.write_manifest('oldcamp', old_hashes, evidence={self.old_rel: self.old_digest})
        old_manifest_sha256 = hashlib.sha256(old_path.read_bytes()).hexdigest()
        self.write_manifest('summarize', self.cur, evidence={self.summary_rel: self.summary_digest},
                            inputs={self.old_rel: {'sha256': self.old_digest, 'manifest': 'oldcamp.json',
                                                    'manifest_sha256': old_manifest_sha256}})

    def test_binding_passes_when_the_input_manifest_is_current(self):
        self.make_summary_manifest(self.cur)
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.binding(self.summary_rel), 'summarize.json')

    def test_binding_refuses_when_the_input_manifest_is_stale(self):
        self.make_summary_manifest(dict(self.cur, src='0' * 64))
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertIsNone(self.br.binding(self.summary_rel))

    def test_binding_refuses_when_the_input_evidence_file_changed_since(self):
        self.make_summary_manifest(self.cur)
        (self.base / self.old_rel).write_text('SYNTHETIC edited after the campaign manifest was written')
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertIsNone(self.br.binding(self.summary_rel))

    def test_cycle_in_inputs_is_invalid_and_terminates(self):
        (self.base / 'x').write_bytes(b'X')
        (self.base / 'y').write_bytes(b'Y')
        dx, dy = (hashlib.sha256((self.base / n).read_bytes()).hexdigest() for n in ('x', 'y'))
        # b is written first so a can correctly pin b's manifest_sha256 for the first hop; b's own reference back
        # to a's manifest_sha256 is never actually read — the cycle is caught by the `seen` name check before that.
        b_path = self.write_manifest('b', self.cur, evidence={'y': dy},
                                     inputs={'x': {'sha256': dx, 'manifest': 'a.json', 'manifest_sha256': 'x'}})
        b_sha256 = hashlib.sha256(b_path.read_bytes()).hexdigest()
        self.write_manifest('a', self.cur, evidence={'x': dx},
                            inputs={'y': {'sha256': dy, 'manifest': 'b.json', 'manifest_sha256': b_sha256}})
        a = json.loads((self.base / 'run-manifests' / 'a.json').read_text())
        self.assertFalse(self.br._inputs_valid(a, self.cur, frozenset({'a.json'})))


# ---- R3-12: a normal rerun of an upstream campaign must invalidate an untouched downstream consumer ------------
class ConsumedDigestPinningTests(Tmp):
    """_inputs_valid must compare the CONSUMER's recorded input digest against the CURRENT file and the
    PRODUCER's current record — not just follow the producer manifest's name. A rerun of the producer under the
    same tree, overwriting both its result file and its manifest (same name, new bytes), makes the current file
    and the producer's fresh record agree with each other while disagreeing with what the consumer actually
    consumed. This drives run_manifest.main() end to end (no hash patching needed: only delivery/ evidence files
    change between steps, and delivery/ is not one of the hashed tree categories)."""

    def setUp(self):
        super().setUp()
        import build_release
        import run_manifest
        self.br, self.rm = build_release, run_manifest
        p = patch.object(self.br, 'D', self.base)
        p.start()
        self.addCleanup(p.stop)
        (self.base / 'eval-report' / 'dev_old').mkdir(parents=True)
        self.old_rel = 'eval-report/dev_old/results_all_runs.jsonl'
        self.summary_rel = 'eval-report/summary.json'

    def produce_old(self, content):
        cmd = [sys.executable, '-c', f'open({str(self.base / self.old_rel)!r}, "w").write({content!r})']
        with patch('sys.stdout'):
            rc = self.rm.main(['oldcamp', '--evidence', self.old_rel, '--', *cmd], delivery=self.base)
        self.assertEqual(rc, 0)

    def consume_old_into_summary(self):
        cmd = [sys.executable, '-c', f'open({str(self.base / self.summary_rel)!r}, "w").write("SYNTHETIC summary")']
        with patch('sys.stdout'):
            rc = self.rm.main(['summarize', '--evidence', self.summary_rel, '--input', self.old_rel,
                               '--', *cmd], delivery=self.base)
        self.assertEqual(rc, 0)

    def test_rerun_of_upstream_overwriting_result_and_manifest_invalidates_the_untouched_summary(self):
        self.produce_old('SYNTHETIC campaign A')
        self.consume_old_into_summary()
        self.assertEqual(self.br.binding(self.summary_rel), 'summarize.json')  # valid right after consumption
        self.produce_old('SYNTHETIC campaign B — a normal rerun, same name, new bytes')  # overwrites oldcamp.json too
        self.assertIsNone(self.br.binding(self.summary_rel))  # summary was never rerun against B; must be refused

    def test_consumer_recorded_digest_disagreeing_with_producer_is_refused(self):
        # Isolates meta['sha256'] (the CONSUMER's own recorded input digest) specifically: the producer manifest's
        # bytes are exactly what the consumer pinned (manifest_sha256 matches) and the current file matches the
        # producer's own evidence record — only the consumer's separately recorded input digest disagrees. A
        # forged/hand-edited consumer input entry looks exactly like this, decoupled from any producer rerun (a
        # real rerun also changes manifest_sha256, which would mask this check if tested only end to end).
        content = b'SYNTHETIC producer bytes'
        (self.base / self.old_rel).write_bytes(content)
        real_digest = hashlib.sha256(content).hexdigest()
        cur = self.rm.tree_hashes()
        producer = self.base / 'run-manifests' / 'oldcamp.json'
        producer.parent.mkdir(parents=True, exist_ok=True)
        producer.write_text(json.dumps({'name': 'oldcamp', 'hashes': cur, 'exit_code': 0,
                                        'tree_changed_during_run': False,
                                        'evidence': {self.old_rel: real_digest}, 'inputs': {}}))
        producer_sha256 = hashlib.sha256(producer.read_bytes()).hexdigest()
        (self.base / self.summary_rel).write_text('SYNTHETIC summary')
        summary_digest = hashlib.sha256((self.base / self.summary_rel).read_bytes()).hexdigest()
        consumer = self.base / 'run-manifests' / 'summarize.json'
        consumer.write_text(json.dumps({'name': 'summarize', 'hashes': cur, 'exit_code': 0,
                                        'tree_changed_during_run': False,
                                        'evidence': {self.summary_rel: summary_digest},
                                        'inputs': {self.old_rel: {'sha256': 'f' * 64,  # disagrees with producer
                                                                  'manifest': 'oldcamp.json',
                                                                  'manifest_sha256': producer_sha256}}}))
        with patch.object(self.br, 'tree_hashes', return_value=cur):
            self.assertIsNone(self.br.binding(self.summary_rel))

    def test_producer_manifest_bytes_changing_invalidates_even_when_evidence_hash_is_unchanged(self):
        # Isolates the manifest_sha256 pin specifically: the evidence file and its recorded hash never change,
        # only the producer manifest's own bytes do (e.g. hand-edited metadata) — meta['sha256'] alone would miss
        # this, since it only compares the input FILE's digest, not the producer manifest's own identity.
        self.produce_old('SYNTHETIC campaign A')
        self.consume_old_into_summary()
        self.assertEqual(self.br.binding(self.summary_rel), 'summarize.json')
        mpath = self.base / 'run-manifests' / 'oldcamp.json'
        rec = json.loads(mpath.read_text())
        rec['note'] = 'tampered after the fact'
        mpath.write_text(json.dumps(rec))
        self.assertIsNone(self.br.binding(self.summary_rel))


# ---- R3-13: eval-report/summary.json has a required, not merely optional, declared input closure ----------------
class SummaryRequiredClosureTests(Tmp):
    def setUp(self):
        super().setUp()
        import build_release
        import run_manifest
        self.br, self.rm = build_release, run_manifest
        p = patch.object(self.br, 'D', self.base)
        p.start()
        self.addCleanup(p.stop)
        self.dirs = {'dev': 'dev_x', 'holdout': 'hold_y'}
        for d in self.dirs.values():
            (self.base / 'eval-report' / d).mkdir(parents=True)
        self.rels = [f'eval-report/{d}/{f}' for d in self.dirs.values()
                    for f in ('results_all_runs.jsonl', 'run_meta.json')]
        for i, rel in enumerate(self.rels):
            cmd = [sys.executable, '-c', f'open({str(self.base / rel)!r}, "w").write("SYNTHETIC {i}")']
            with patch('sys.stdout'):
                rc = self.rm.main([f'p{i}', '--evidence', rel, '--', *cmd], delivery=self.base)
            self.assertEqual(rc, 0)
        self.cur = self.rm.tree_hashes()
        self.summary_rel = 'eval-report/summary.json'
        (self.base / self.summary_rel).write_text(json.dumps({'status': 'PASS', 'dev_round': self.dirs['dev'],
                                                               'holdout_round': self.dirs['holdout']}))

    def write_summary_manifest(self, rels_to_declare):
        digest = hashlib.sha256((self.base / self.summary_rel).read_bytes()).hexdigest()
        inputs = {rel: self.rm.validate_input(self.base, rel, self.cur) for rel in rels_to_declare}
        (self.base / 'run-manifests' / 'summarize.json').write_text(json.dumps({
            'name': 'summarize', 'hashes': self.cur, 'exit_code': 0, 'tree_changed_during_run': False,
            'evidence': {self.summary_rel: digest}, 'inputs': inputs}))

    def test_full_closure_passes(self):
        self.write_summary_manifest(self.rels)
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.check('PASS', self.summary_rel, 'n')['status'], 'PASS')

    def test_missing_run_meta_inputs_is_refused(self):
        only_results = [r for r in self.rels if r.endswith('results_all_runs.jsonl')]
        self.write_summary_manifest(only_results)
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            r = self.br.check('PASS', self.summary_rel, 'n')
        self.assertEqual(r['status'], 'NOT_RUN')
        for missing in (f'eval-report/{d}/run_meta.json' for d in self.dirs.values()):
            self.assertIn(missing, r['notes'])

    def test_a_summary_without_valid_campaign_names_is_refused(self):
        self.write_summary_manifest(self.rels)
        for bad in ({}, {'dev_round': '', 'holdout_round': self.dirs['holdout']},
                    {'dev_round': '../x', 'holdout_round': self.dirs['holdout']}):
            (self.base / self.summary_rel).write_text(json.dumps({'status': 'PASS', **bad}))
            self.write_summary_manifest(self.rels)
            with patch.object(self.br, 'tree_hashes', return_value=self.cur):
                self.assertEqual(self.br.check('PASS', self.summary_rel, 'n')['status'], 'NOT_RUN', bad)

    def test_missing_one_campaigns_results_file_is_refused(self):
        only_dev = [r for r in self.rels if f'/{self.dirs["dev"]}/' in r]
        self.write_summary_manifest(only_dev)
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            r = self.br.check('PASS', self.summary_rel, 'n')
        self.assertEqual(r['status'], 'NOT_RUN')


# ---- R2-12: summarize validates rows and prompts against campaign identity, not just internal consistency ------
class SummarizeForeignCampaignTests(Tmp):
    def setUp(self):
        super().setUp()
        self.cases = [json.loads(x) for x in (ROOT / 'evals/cases.jsonl').read_text().splitlines() if x.strip()]
        self.meta = {'model': 'm', 'judge_model': 'j', 'backend': 'b', 'hashes': PROMPTS}

    def write(self, swap_case=None, swap_model=None, swap_prompt=None):
        for split in ('dev', 'holdout'):
            d = self.base / split
            d.mkdir(exist_ok=True)
            (d / 'run_meta.json').write_text(json.dumps(self.meta))
            rows = []
            for c in self.cases:
                if c['split'] != split:
                    continue
                model_id, prompt = 'm', 'p' * 64
                if c['id'] == swap_case:
                    model_id = swap_model or model_id
                    prompt = swap_prompt or prompt
                for i in range(3 if c['severity'] == 'critical' else 1):
                    rows.append({'case_id': c['id'], 'run': i + 1, 'status': 'PASS', 'hard_failure': False,
                                 'reason': 'SYNTHETIC ok', 'model_id': model_id, 'prompt_sha256': prompt, 'mode': 'foreground',
                                 'trace_id': 't', 'evidence_file': 'e.json'})
            (d / 'results_all_runs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))

    def run_summary(self):
        out = self.base / 'report' / 'summary.json'
        out.parent.mkdir(exist_ok=True)
        p = subprocess.run([sys.executable, str(ROOT / 'evals/summarize.py'), str(self.base / 'dev'),
                            str(self.base / 'holdout'), str(out)], capture_output=True, text=True)
        return p.returncode, json.loads(out.read_text())

    def test_clean_campaign_has_no_foreign_flags(self):
        self.write()
        rc, s = self.run_summary()
        self.assertEqual((rc, s['status'], s['foreign_rows'], s['foreign_prompt_cases']), (0, 'PASS', [], []))

    def test_real_committed_campaign_has_no_foreign_flags(self):
        out = self.base / 'real' / 'summary.json'
        out.parent.mkdir()
        er = ROOT / 'delivery' / 'eval-report'
        subprocess.run([sys.executable, str(ROOT / 'evals/summarize.py'), str(er / 'dev_c519ba9'),
                        str(er / 'holdout_c519ba9'), str(out)], capture_output=True, text=True)
        s = json.loads(out.read_text())
        self.assertEqual((s['foreign_rows'], s['foreign_prompt_cases'], s['mixed_campaign_cases']), ([], [], []))

    def test_whole_case_from_a_foreign_model_campaign_is_flagged(self):
        target = next(c['id'] for c in self.cases if c['split'] == 'dev')
        self.write(swap_case=target, swap_model='other-model')
        rc, s = self.run_summary()
        self.assertEqual(rc, 1)
        self.assertEqual(s['foreign_rows'], [target])
        self.assertEqual(s['foreign_prompt_cases'], [])

    def test_whole_case_from_a_foreign_prompt_campaign_is_flagged(self):
        target = next(c['id'] for c in self.cases if c['split'] == 'dev')
        self.write(swap_case=target, swap_prompt='f' * 64)
        rc, s = self.run_summary()
        self.assertEqual(rc, 1)
        self.assertEqual(s['foreign_prompt_cases'], [target])
        self.assertEqual(s['foreign_rows'], [])


# ---- R2-13: live() reads the structured JSON probe, not a textual src-tree marker -------------------------------
class LiveProbeTests(Tmp):
    def setUp(self):
        super().setUp()
        import build_release
        import run_manifest
        self.br, self.rm = build_release, run_manifest
        p = patch.object(self.br, 'D', self.base)
        p.start()
        self.addCleanup(p.stop)
        (self.base / 'live-evidence').mkdir()
        (self.base / 'eval-report').mkdir()
        self.cur = self.rm.tree_hashes()
        self.artifact_rel = 'eval-report/summary.json'
        (self.base / self.artifact_rel).write_text('SYNTHETIC summary for live probe')
        self.artifact_digest = hashlib.sha256((self.base / self.artifact_rel).read_bytes()).hexdigest()

    ALL_NEEDED = ('write', 'fresh_read', 'file_image', 'file_pdf', 'real_data_investigation')

    def write_probe(self, inputs=None, artifacts=None, probes=None):
        doc = {'observed_at': '2026-09-24T00:00:00+00:00',
               'inputs': self.cur if inputs is None else inputs,
               'artifacts': {self.artifact_rel: self.artifact_digest} if artifacts is None else artifacts,
               'probes': probes if probes is not None else
               {name: {'status': 'PASS', 'artifacts': [self.artifact_rel]} for name in self.ALL_NEEDED}}
        (self.base / self.br.LIVE).write_text(json.dumps(doc))

    def test_pass_when_inputs_artifacts_and_probes_all_match(self):
        self.write_probe()
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'PASS')

    def test_not_run_when_a_structural_input_hash_differs(self):
        bad = dict(self.cur, prompts='0' * 64)
        self.write_probe(inputs=bad)
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')

    def test_not_run_when_an_artifact_hash_differs(self):
        self.write_probe(artifacts={self.artifact_rel: '0' * 64})
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')

    def test_not_run_when_a_needed_probe_is_not_pass(self):
        self.write_probe(probes={'write': {'status': 'FAIL', 'artifacts': []},
                                 'fresh_read': {'status': 'PASS', 'artifacts': [self.artifact_rel]}})
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')

    def test_a_different_checks_missing_probe_does_not_block_this_one(self):
        self.write_probe(probes={'write': {'status': 'PASS', 'artifacts': [self.artifact_rel]},
                                 'fresh_read': {'status': 'PASS', 'artifacts': [self.artifact_rel]}})
        # file_image/file_pdf absent entirely
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'PASS')
            self.assertEqual(self.br.live('chatgpt_files', 'n')['status'], 'NOT_RUN')

    def test_not_run_when_a_pass_probe_lists_no_artifacts(self):
        # Distinguishes recording a probe from re-performing it: a bare status assertion with nothing to check
        # independently must not count as PASS. fresh_read (the check's other required probe) is otherwise
        # perfectly valid, so a false pass here could only come from the empty-artifacts case being overlooked,
        # not from some other missing-probe reason.
        self.write_probe(probes={'write': {'status': 'PASS', 'artifacts': []},
                                 'fresh_read': {'status': 'PASS', 'artifacts': [self.artifact_rel]}})
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')

    def test_not_run_when_a_pass_probes_listed_artifact_is_not_in_the_top_level_map(self):
        self.write_probe(probes={'write': {'status': 'PASS', 'artifacts': ['eval-report/not_recorded.json']},
                                 'fresh_read': {'status': 'PASS', 'artifacts': [self.artifact_rel]}})
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')


# ---- R3-13 (live attestation): scripts/record_live_probe.py writes the per-probe-artifact shape live() expects --
class RecordLiveProbeTests(Tmp):
    def setUp(self):
        super().setUp()
        import record_live_probe
        self.rlp = record_live_probe
        (self.base / 'eval-report').mkdir()
        self.art1 = self.base / 'eval-report' / 'summary.json'
        self.art1.write_text('SYNTHETIC summary')
        self.art2 = self.base / 'eval-report' / 'other.json'
        self.art2.write_text('SYNTHETIC other')

    def test_writes_expected_shape_with_per_probe_artifacts(self):
        with patch('sys.stdout'):
            rc = self.rlp.main(
                ['--probe', 'write=PASS:eval-report/summary.json,eval-report/other.json',
                 '--probe', 'fresh_read=PASS:eval-report/summary.json'],
                delivery=self.base)
        self.assertEqual(rc, 0)
        doc = json.loads((self.base / 'live-evidence' / 'chatgpt_live_probe.json').read_text())
        self.assertEqual(doc['probes']['write']['status'], 'PASS')
        self.assertEqual(set(doc['probes']['write']['artifacts']),
                         {'eval-report/summary.json', 'eval-report/other.json'})
        self.assertEqual(doc['probes']['fresh_read'], {'status': 'PASS', 'artifacts': ['eval-report/summary.json']})
        self.assertEqual(set(doc['inputs']), {'src', 'contracts', 'prompts', 'ops'})
        self.assertEqual(doc['artifacts']['eval-report/summary.json'],
                         hashlib.sha256(self.art1.read_bytes()).hexdigest())
        self.assertEqual(doc['artifacts']['eval-report/other.json'],
                         hashlib.sha256(self.art2.read_bytes()).hexdigest())

    def test_probe_with_no_artifacts_is_allowed_to_record_but_will_read_not_run_later(self):
        with patch('sys.stdout'):
            rc = self.rlp.main(['--probe', 'write=FAIL'], delivery=self.base)
        self.assertEqual(rc, 0)
        doc = json.loads((self.base / 'live-evidence' / 'chatgpt_live_probe.json').read_text())
        self.assertEqual(doc['probes']['write'], {'status': 'FAIL', 'artifacts': []})

    def test_unknown_artifact_path_is_refused(self):
        with patch('sys.stdout'), patch('sys.stderr'), self.assertRaises(SystemExit):
            self.rlp.main(['--probe', 'write=PASS:eval-report/does_not_exist.json'], delivery=self.base)


# ---- Exact package claim: git archive HEAD, dirty refusal, generated attestations go to dist/ not delivery/ -----
class PackageReleaseGitArchiveTests(unittest.TestCase):
    """Runs scripts/package_release.main() against a throwaway git repo (ROOT patched), never the real repo, so
    it's unaffected by whatever this session's own working tree looks like."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.email', 'synthetic@example.com'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'synthetic'], cwd=self.repo, check=True)
        (self.repo / 'a.txt').write_text('SYNTHETIC A')
        (self.repo / 'delivery').mkdir()
        (self.repo / 'delivery' / 'b.json').write_text('{}')
        subprocess.run(['git', 'add', 'a.txt', 'delivery/b.json'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=self.repo, check=True)
        import package_release
        self.pr = package_release
        p = patch.object(self.pr, 'ROOT', self.repo)
        p.start()
        self.addCleanup(p.stop)

    def test_scan_report_and_manifest_land_in_dist_not_delivery(self):
        with patch('sys.stdout'):
            rc = self.pr.main()
        self.assertEqual(rc, 0)
        self.assertTrue((self.repo / 'dist' / 'manifest.sha256').is_file())
        self.assertTrue((self.repo / 'dist' / 'package_scan.json').is_file())
        self.assertFalse((self.repo / 'delivery' / 'manifest.sha256').exists())
        self.assertFalse((self.repo / 'delivery' / 'package_scan.json').exists())

    def test_dirty_tracked_tree_is_refused_and_nothing_is_written(self):
        (self.repo / 'a.txt').write_text('SYNTHETIC A, changed after commit')
        with patch('sys.stdout'):
            rc = self.pr.main()
        self.assertEqual(rc, 1)
        self.assertFalse((self.repo / 'dist').exists())

    def test_archive_bytes_equal_git_show_head(self):
        with patch('sys.stdout'):
            rc = self.pr.main()
        self.assertEqual(rc, 0)
        arcs = list((self.repo / 'dist').glob('*.tar.gz'))
        self.assertEqual(len(arcs), 1)
        with tarfile.open(arcs[0], 'r:gz') as tar:
            member = tar.getmember('personal-health-context/a.txt')
            got = tar.extractfile(member).read()
        want = subprocess.run(['git', 'show', 'HEAD:a.txt'], cwd=self.repo, capture_output=True).stdout
        self.assertEqual(got, want)


if __name__ == '__main__':
    unittest.main(verbosity=2)
