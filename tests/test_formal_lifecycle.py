"""Rehearse the evaluator's directory/env transitions with real CPU HTTP services.

This verifies deployment orchestration, not CUDA, model numerics, or scoring.
An isolated checkout prevents a no-environment stop from affecting a user's service.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import httpx
import psutil

from tests.support import LocalStack, ROOT


class FormalLifecycleTests(unittest.TestCase):
    def test_custom_data_stop_and_profile_switch_with_minimal_environment(self):
        output = Path(os.environ['TEST_RESULTS']) / 'formal-lifecycle'
        stack = LocalStack(output, protocol=True)
        checkout = stack.runtime / 'checkout'
        checkout.mkdir()
        for folder in ('server', 'control_plane', 'agent', 'migrations', 'configs', 'console', 'experiments'):
            shutil.copytree(ROOT / folder, checkout / folder,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for filename in ('setup.sh', 'requirements.lock'):
            shutil.copy2(ROOT / filename, checkout / filename)
        phases = []

        def setup(arguments, env):
            # Ubuntu CI exercises the actual shell entrypoint. Windows uses its
            # identical Python bootstrap because the deployment target is Linux.
            command = (['bash', 'setup.sh'] if sys.platform == 'linux'
                       else [sys.executable, '-m', 'server.bootstrap']) + arguments
            result = subprocess.run(command, cwd=checkout, env=env, capture_output=True,
                                    text=True, timeout=180)
            text = result.stdout + result.stderr
            for secret in stack.secrets:
                text = text.replace(secret, '[REDACTED]')
            with (output / 'commands.txt').open('a', encoding='utf-8') as log:
                log.write(json.dumps(command) + '\n' + text + '\n')
            self.assertEqual(result.returncode, 0, text[-3000:])

        def pid(data):
            return int((data / 'gateway.pid').read_text())

        def stopped(data, previous):
            self.assertFalse(psutil.pid_exists(previous))
            self.assertEqual(json.loads((data / 'processes.json').read_text())['processes'], [])
            self.assertFalse((data / 'gateway.pid').exists())
            self.assertTrue((data / 'ledger.sqlite3').is_file())
            with httpx.Client(trust_env=False, timeout=2) as client:
                self.assertEqual(client.get(stack.fixture + '/health').status_code, 200)

        # The evaluator drops service credentials and DATA_DIR when stopping.
        minimal = os.environ.copy()
        for key in ('DATA_DIR', 'API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET',
                    'CONTRACT_TEST', 'BENCH_PROFILE', 'ENGINE_BASE_URL'):
            minimal.pop(key, None)
        credential_data = stack.data
        serving_data = stack.runtime / 'serving-data'
        try:
            with (stack.runtime / 'fixture.log').open('ab') as log:
                stack.fixture_process = subprocess.Popen(
                    [sys.executable, str(stack.fixture_script)], cwd=checkout,
                    env=stack.env, stdout=log, stderr=log,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            stack.wait(stack.fixture + '/health')
            setup(['--contract-test'], stack.env)
            stack.wait(stack.base + '/healthz')
            credential_pid = pid(credential_data)
            sentinel = credential_data / 'preserve-evidence.txt'
            sentinel.write_text('owned data survives phase changes', encoding='utf-8')
            setup(['--stop'], minimal)
            stopped(credential_data, credential_pid)
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'owned data survives phase changes')
            phases.append('custom CPU DATA_DIR -> no-env setup --stop; fixture and ledger preserved')

            # Real gateway/profile transitions with the explicit CPU fixture;
            # this deliberately does not claim GPU startup was exercised here.
            stack.env.update(DATA_DIR=str(serving_data), BENCH_PROFILE='baseline')
            setup(['--contract-test'], stack.env)
            stack.wait(stack.base + '/healthz')
            baseline_pid = pid(serving_data)
            phases.append('same gateway port reused with a different DATA_DIR and baseline profile')
            # Use the real experiment entrypoint and registry in a fresh process.
            # Only CUDA and numerical work are replaced; it must stop the real
            # gateway with the minimal environment used by the evaluator.
            program = '''
import sys
from unittest.mock import patch
import httpx
import experiments.run as entry
from server.supervisor import port_free
case, out, port, fixture = sys.argv[1:]
def numerical_double(path, info, settings):
    port_free('127.0.0.1', int(port))
    with httpx.Client(trust_env=False, timeout=2) as client:
        assert client.get(fixture + '/health').status_code == 200
    info['test_scope'] = 'CPU orchestration only; CUDA and numerical work replaced'
    return True
sys.argv = ['experiment', '--case', case, '--out', out]
with patch.object(entry, 'require_cuda'), patch('experiments.' + case + '.run', numerical_double):
    raise SystemExit(entry.main())
'''
            for case in ('kv', 'prefix', 'attention'):
                case_output = stack.runtime / ('cpu-orchestration-' + case)
                result = subprocess.run([sys.executable, '-c', program, case, str(case_output),
                                         str(stack.port), stack.fixture], cwd=checkout, env=minimal,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads((case_output / 'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual(report['test_scope'],
                                 'CPU orchestration only; CUDA and numerical work replaced')
                self.assertTrue(report['complete'])
            stopped(serving_data, baseline_pid)
            phases.append('three minimal-env experiment entrypoints release real services; numerical work uses CPU doubles')
            stack.env['BENCH_PROFILE'] = 'optimized'
            setup(['--contract-test'], stack.env)
            stack.wait(stack.base + '/healthz')
            optimized_pid = pid(serving_data)
            self.assertNotEqual(baseline_pid, optimized_pid)
            phases.append('optimized profile starts after mechanism entrypoints')
            setup(['--contract-test'], stack.env)
            self.assertEqual(pid(serving_data), optimized_pid)
            phases.append('identical setup preserves the listening PID')
            setup(['--stop'], minimal)
            stopped(serving_data, optimized_pid)
            setup(['--stop'], minimal)
            phases.append('no-env stop is repeatable and leaves the external fixture alive')
        finally:
            # Target only this isolated checkout and its two known directories,
            # even when an assertion fails before the new discovery logic works.
            cleanup_errors = []
            for data in (credential_data, serving_data):
                try:
                    cleanup = dict(stack.env, DATA_DIR=str(data))
                    subprocess.run([sys.executable, '-m', 'server.supervisor', '--stop'],
                                   cwd=checkout, env=cleanup, capture_output=True, timeout=25, check=True)
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(type(error).__name__ + ': ' + data.name)
            try:
                if stack.fixture_process and stack.fixture_process.poll() is None:
                    stack.fixture_process.terminate()
                    try:
                        stack.fixture_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        stack.fixture_process.kill()
                        stack.fixture_process.wait()
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_errors.append(type(error).__name__ + ': external fixture')
            (output / 'phases.json').write_text(json.dumps({
                'verified_phases': phases, 'execution_environment': sys.platform,
                'gpu_exercised': False, 'formal_evaluation_submitted': False,
                'cleanup_errors': cleanup_errors,
                'external_fixture_stopped_by_test_driver': stack.fixture_process is not None
                    and stack.fixture_process.poll() is not None,
            }, ensure_ascii=False, indent=2), encoding='utf-8')
            self.assertEqual(cleanup_errors, [], 'Test cleanup failed; all known directories were attempted')
