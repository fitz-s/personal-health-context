import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_gate',ROOT/'scripts/release_gate.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        p=self.root/'test-evidence.txt';p.write_text('SYNTHETIC gate fixture, not actual deployment evidence.')
        sha=hashlib.sha256(p.read_bytes()).hexdigest()
        self.r={'claimed_status':'READY','gaps':[],'checks':{x:{'status':'PASS','evidence':p.name,'evidence_sha256':sha,'notes':'Synthetic gate test only.'} for x in gate.CHECKS}}
    def tearDown(self):self.tmp.cleanup()
    def test_all_evidence_shape_passes(self):
        self.assertTrue(gate.assess(self.r,self.root)['valid'])
    def test_not_run_cannot_claim_ready(self):
        self.r['checks']['oura_official_access']['status']='NOT_RUN'
        self.assertFalse(gate.assess(self.r,self.root)['valid'])
    def test_honest_external_gap(self):
        self.r['checks']['oura_official_access']['status']='BLOCKED'
        self.r['claimed_status']='READY_WITH_GAPS';self.r['gaps']=['Oura account unavailable.']
        self.assertTrue(gate.assess(self.r,self.root)['valid'])
    def test_core_not_run_blocks(self):
        self.r['checks']['auth_and_policy']['status']='NOT_RUN'
        self.assertEqual(gate.assess(self.r,self.root)['derived_status'],'BLOCKED')
    def test_failed_quality_blocks(self):
        self.r['checks']['model_eval']['status']='FAIL'
        self.assertEqual(gate.assess(self.r,self.root)['derived_status'],'BLOCKED')
    def test_evidence_hash_mismatch(self):
        self.r['checks']['unit_regression']['evidence_sha256']='0'*64
        self.assertFalse(gate.assess(self.r,self.root)['valid'])
    def test_evidence_path_traversal(self):
        self.r['checks']['unit_regression']['evidence']='../outside.txt'
        self.assertFalse(gate.assess(self.r,self.root)['valid'])
    def test_missing_check(self):
        del self.r['checks']['unit_regression']
        self.assertFalse(gate.assess(self.r,self.root)['valid'])
