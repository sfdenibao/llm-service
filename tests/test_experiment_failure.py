"""Failure artifacts must preserve diagnosis without leaking runtime secrets."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.config import ExperimentSettings, cache_environment
from experiments.run import ExperimentTerminated, main, termination_guard


class ExperimentFailureTests(unittest.TestCase):
    def test_failure_evidence_is_incomplete_and_redacted(self):
        secret='synthetic-secret-only-for-redaction-test'
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'failed-run'
            with patch.dict(os.environ,{'API_KEY':secret}), \
                 patch('sys.argv',['experiment','--case','attention','--out',str(out)]), \
                 patch('experiments.run.require_cuda',side_effect=RuntimeError('CUDA probe failed '+secret)):
                self.assertEqual(main(),1)
            manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
            detail=json.loads((out/'failure.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['complete'])
            self.assertEqual(manifest['failure']['evidence'],'failure.json')
            self.assertIn('CUDA probe failed',detail['traceback'])
            self.assertIn('Traceback',detail['traceback'])
            self.assertNotIn(secret,(out/'failure.json').read_text(encoding='utf-8'))
            self.assertIn('[REDACTED]',detail['traceback'])

    def test_formal_case_routes_without_service_credentials_using_cpu_doubles(self):
        # These doubles validate entrypoint orchestration only, never GPU calculations.
        for case in ('kv', 'prefix', 'attention'):
            for outcome in ('pass', 'numerical_failure', 'interrupted', 'sigterm'):
                with self.subTest(case=case, outcome=outcome), tempfile.TemporaryDirectory() as directory:
                    out=Path(directory)/'run'
                    events=[]
                    @contextmanager
                    def stopped_double():
                        events.append('locked')
                        try:
                            yield {'HF_HOME':'/cpu-double/cache','HF_HUB_CACHE':'/cpu-double/cache/hub'}
                        finally:
                            events.append('unlocked')
                    def case_double(path, info, settings):
                        self.assertEqual(events, ['locked'])
                        self.assertEqual(path, out.resolve())
                        self.assertIsInstance(settings, ExperimentSettings)
                        self.assertEqual(settings.model, 'declared-model')
                        self.assertEqual(settings.revision, 'declared-revision')
                        self.assertEqual(settings.profile, 'optimized')
                        self.assertEqual(os.environ['HF_HOME'], '/cpu-double/cache')
                        self.assertEqual(os.environ['HF_HUB_CACHE'], '/cpu-double/cache/hub')
                        self.assertEqual(settings.config['dtype'], 'bfloat16')
                        self.assertEqual(settings.engine_url, 'http://127.0.0.1:30001')
                        for name in ('api_key','admin_key','pepper','data_dir'):
                            self.assertFalse(hasattr(settings,name))
                        info['test_scope']='CPU orchestration double; GPU numerical behavior not tested'
                        if outcome=='interrupted':
                            raise RuntimeError('Synthetic case interruption')
                        if outcome=='sigterm':
                            signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
                        return outcome=='pass'
                    env={'MODEL_ID':'declared-model','MODEL_REVISION':'declared-revision'}
                    with patch.dict(os.environ,env,clear=True), \
                         patch('sys.argv',['experiment','--case',case,'--out',str(out)]), \
                         patch('experiments.run.manifest',return_value={'case':case,'complete':False}), \
                         patch('experiments.run.require_cuda') as cuda, \
                         patch('server.config.Settings.load',side_effect=AssertionError('Service config must not load')), \
                         patch('experiments.run.stopped_services',stopped_double), \
                         patch('experiments.'+case+'.run',side_effect=case_double) as run:
                        self.assertEqual(main(),0 if outcome=='pass' else 1)
                        cuda.assert_called_once()
                        run.assert_called_once()
                        self.assertNotIn('HF_HOME',os.environ)
                        self.assertNotIn('HF_HUB_CACHE',os.environ)
                        for name in ('API_KEY','ADMIN_API_KEY','KEY_HASH_SECRET','DATA_DIR'):
                            self.assertNotIn(name,os.environ)
                    self.assertEqual(events,['locked','unlocked'])
                    info=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
                    interrupted=outcome in ('interrupted','sigterm')
                    self.assertEqual(info['complete'],not interrupted)
                    self.assertEqual((out/'failure.json').exists(),interrupted)
                    if interrupted:
                        failure=json.loads((out/'failure.json').read_text(encoding='utf-8'))
                        message='Synthetic case interruption' if outcome=='interrupted' else 'SIGTERM'
                        self.assertIn(message,failure['traceback'])

    def test_contract_mode_refused_before_stopping_services(self):
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'refused'
            with patch.dict(os.environ,{'CONTRACT_TEST':'1'},clear=True), \
                 patch('sys.argv',['experiment','--case','attention','--out',str(out)]), \
                 patch('experiments.run.manifest',return_value={'case':'attention','complete':False}), \
                 patch('experiments.run.require_cuda') as cuda, \
                 patch('experiments.run.stopped_services') as stopped:
                self.assertEqual(main(),1)
                cuda.assert_not_called()
                stopped.assert_not_called()
            self.assertFalse(json.loads((out/'manifest.json').read_text(encoding='utf-8'))['complete'])

    def test_real_cuda_gate_rejects_cpu_before_stopping_services(self):
        cpu_torch=SimpleNamespace(cuda=SimpleNamespace(is_available=lambda:False))
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'no-cuda'
            with patch.dict(os.environ,{},clear=True), \
                 patch.dict(sys.modules,{'torch':cpu_torch}), \
                 patch('sys.argv',['experiment','--case','kv','--out',str(out)]), \
                 patch('experiments.run.manifest',return_value={'case':'kv','complete':False}), \
                 patch('experiments.run.stopped_services') as stopped:
                self.assertEqual(main(),1)
                stopped.assert_not_called()
            failure=json.loads((out/'failure.json').read_text(encoding='utf-8'))
            self.assertIn('CUDA is not available',failure['traceback'])

    def test_termination_guard_ignores_repeated_term_and_restores_previous_handler(self):
        previous=signal.getsignal(signal.SIGTERM)
        with termination_guard():
            with self.assertRaises(ExperimentTerminated):
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
            self.assertEqual(signal.getsignal(signal.SIGTERM),signal.SIG_IGN)
        self.assertEqual(signal.getsignal(signal.SIGTERM),previous)


class ExperimentConfigurationTests(unittest.TestCase):
    def test_invalid_profile_and_nonlocal_engine_are_rejected(self):
        for env in ({'BENCH_PROFILE':'unknown'}, {'ENGINE_BASE_URL':'https://127.0.0.1:30001'},
                    {'ENGINE_BASE_URL':'http://192.0.2.1:30001'}, {'ENGINE_BASE_URL':'http://127.0.0.1:30001/api'}):
            with self.subTest(env=env), patch.dict(os.environ,env,clear=True):
                with self.assertRaises(ValueError):ExperimentSettings.load()

    def test_saved_cache_is_temporary_and_explicit_cache_selection_wins(self):
        saved={'HF_HOME':'/saved/home','HF_HUB_CACHE':'/saved/hub'}
        for explicit in ({}, {'HF_HOME':'/explicit/home'}, {'HF_HUB_CACHE':'/explicit/hub'},
                         {'HUGGINGFACE_HUB_CACHE':'/explicit/legacy'}, {'HF_HOME':''}):
            with self.subTest(explicit=explicit), patch.dict(os.environ,explicit,clear=True):
                with self.assertRaisesRegex(RuntimeError,'test exit'):
                    with cache_environment(saved):
                        self.assertEqual(dict(os.environ),explicit if explicit else saved)
                        raise RuntimeError('test exit')
                self.assertEqual(dict(os.environ),explicit)
