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
        (self.base / 'run-manifests' / f'{name}.json').write_text(json.dumps({
            'name': name, 'hashes': hashes, 'exit_code': exit_code, 'tree_changed_during_run': tree_changed,
            'evidence': evidence or {}, 'inputs': inputs or {}}))

    def make_summary_manifest(self, old_hashes):
        self.write_manifest('oldcamp', old_hashes, evidence={self.old_rel: self.old_digest})
        self.write_manifest('summarize', self.cur, evidence={self.summary_rel: self.summary_digest},
                            inputs={self.old_rel: {'sha256': self.old_digest, 'manifest': 'oldcamp.json'}})

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
        self.write_manifest('a', self.cur, evidence={'x': dx}, inputs={'y': {'sha256': dy, 'manifest': 'b.json'}})
        self.write_manifest('b', self.cur, evidence={'y': dy}, inputs={'x': {'sha256': dx, 'manifest': 'a.json'}})
        a = json.loads((self.base / 'run-manifests' / 'a.json').read_text())
        self.assertFalse(self.br._inputs_valid(a, self.cur, frozenset({'a.json'})))


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

    def write_probe(self, inputs=None, artifacts=None, probes=None):
        doc = {'observed_at': '2026-09-24T00:00:00+00:00',
               'inputs': self.cur if inputs is None else inputs,
               'artifacts': {self.artifact_rel: self.artifact_digest} if artifacts is None else artifacts,
               'probes': probes if probes is not None else
               {'write': 'PASS', 'fresh_read': 'PASS', 'file_image': 'PASS', 'file_pdf': 'PASS',
                'real_data_investigation': 'PASS'}}
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
        self.write_probe(probes={'write': 'FAIL', 'fresh_read': 'PASS'})
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'NOT_RUN')

    def test_a_different_checks_missing_probe_does_not_block_this_one(self):
        self.write_probe(probes={'write': 'PASS', 'fresh_read': 'PASS'})  # file_image/file_pdf absent
        with patch.object(self.br, 'tree_hashes', return_value=self.cur):
            self.assertEqual(self.br.live('chatgpt_read_write', 'n')['status'], 'PASS')
            self.assertEqual(self.br.live('chatgpt_files', 'n')['status'], 'NOT_RUN')


# ---- R2-13: scripts/record_live_probe.py writes the shape build_release.live() expects -------------------------
class RecordLiveProbeTests(Tmp):
    def test_writes_expected_shape(self):
        import record_live_probe
        (self.base / 'eval-report').mkdir()
        art = self.base / 'eval-report' / 'summary.json'
        art.write_text('SYNTHETIC summary')
        with patch('sys.stdout'):
            rc = record_live_probe.main(
                ['--probe', 'write=PASS', '--probe', 'fresh_read=PASS', '--artifact', 'eval-report/summary.json'],
                delivery=self.base)
        self.assertEqual(rc, 0)
        doc = json.loads((self.base / 'live-evidence' / 'chatgpt_live_probe.json').read_text())
        self.assertEqual(doc['probes'], {'write': 'PASS', 'fresh_read': 'PASS'})
        self.assertEqual(set(doc['inputs']), {'src', 'contracts', 'prompts', 'ops'})
        self.assertEqual(doc['artifacts']['eval-report/summary.json'],
                         hashlib.sha256(art.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main(verbosity=2)
