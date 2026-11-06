"""Real Linux signals/session cleanup; no GPU, model or remote service is used."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil

from experiments.processes import owned_session


ROOT = Path(__file__).resolve().parents[1]
CHILD = r'''
import os,signal,sys,time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if sys.argv[2] == 'new-group': os.setpgid(0, 0)
Path(sys.argv[1]).write_text('ready')
time.sleep(60)
'''
TREE = r'''
import json,os,signal,subprocess,sys,time
from pathlib import Path
import psutil
signal.signal(signal.SIGTERM, signal.SIG_IGN)
ready = Path(sys.argv[1] + '.child-ready')
child = subprocess.Popen([sys.executable, '-c', sys.argv[3], str(ready), sys.argv[4]])
deadline = time.monotonic() + 5
while not ready.exists():
    if time.monotonic() > deadline: raise RuntimeError('test child did not start')
    time.sleep(.01)
Path(sys.argv[1]).write_text(json.dumps([
    {'pid': p.pid, 'created': p.create_time()} for p in (psutil.Process(), psutil.Process(child.pid))]))
if sys.argv[2] == 'exit-parent': sys.exit(0)
time.sleep(60)
'''


def wait_path(path, timeout=8):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for synthetic process readiness')
        time.sleep(.02)


def states(records):
    result = []
    for item in records:
        try:
            p = psutil.Process(item['pid'])
            value = p.status() if abs(p.create_time() - item['created']) < .01 else 'pid-reused'
        except psutil.NoSuchProcess:
            value = 'absent'
        result.append({'pid': item['pid'], 'state': value})
    return result


def cleanup_records(records):
    for item in records:
        try:
            p = psutil.Process(item['pid'])
            if abs(p.create_time() - item['created']) < .01:
                p.kill()
        except psutil.NoSuchProcess:
            pass


class ExperimentProcessPlatformTests(unittest.TestCase):
    def test_non_linux_refuses_before_spawning(self):
        with patch('experiments.processes.sys.platform', 'win32'), \
             patch('experiments.processes.subprocess.Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'require Linux'):
                with owned_session(['must-not-spawn']):
                    self.fail('must not enter')
            popen.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux', 'Requires real Linux sessions and SIGTERM; no Windows emulation')
class ExperimentProcessLinuxTests(unittest.TestCase):
    def write_evidence(self, value):
        if os.environ.get('TEST_RESULTS'):
            directory = Path(os.environ['TEST_RESULTS']) / 'experiment-processes'
            directory.mkdir(parents=True, exist_ok=True)
            value = {'passed': True, 'scope': 'Synthetic Linux processes only; no GPU inference', **value}
            (directory / (self._testMethodName + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')

    def assert_stopped(self, records):
        observed = states(records)
        self.assertTrue(all(p['state'] in ('absent', psutil.STATUS_ZOMBIE, 'pid-reused') for p in observed), observed)
        return observed

    def test_exited_leader_and_child_in_different_group_are_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            pids = Path(directory) / 'pids.json'
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
            records = []
            try:
                started = time.monotonic()
                with owned_session([sys.executable, '-c', TREE, str(pids), 'exit-parent', CHILD, 'new-group'],
                                   stdout=subprocess.DEVNULL, term_timeout=.2, kill_timeout=2) as process:
                    wait_path(pids)
                    records = json.loads(pids.read_text())
                    self.assertEqual(process.wait(timeout=5), 0)
                    self.assertNotEqual(os.getpgid(records[1]['pid']), process.pid)
                    self.assertEqual(os.getsid(records[1]['pid']), process.pid)
                elapsed = time.monotonic() - started
                observed = self.assert_stopped(records)
                self.assertIsNone(unrelated.poll())
                self.assertLess(elapsed, 5)
                self.write_evidence({'process_states': observed, 'parent_exited_before_cleanup': True,
                                     'child_changed_process_group': True, 'unrelated_process_alive': True,
                                     'elapsed_seconds': elapsed})
            finally:
                cleanup_records(records)
                unrelated.kill(); unrelated.wait(timeout=3)

    def test_term_during_registration_waits_until_owner_finally_exists(self):
        from experiments.processes import OwnedSession
        from experiments.run import ExperimentTerminated, termination_guard
        with tempfile.TemporaryDirectory() as directory:
            pids = Path(directory) / 'pids.json'
            records = []
            original = OwnedSession.__init__

            def interrupted_registration(owner, process, **kwargs):
                original(owner, process, **kwargs)
                wait_path(pids)
                os.kill(os.getpid(), signal.SIGTERM)

            try:
                with termination_guard(), patch.object(OwnedSession, '__init__', interrupted_registration):
                    with self.assertRaises(ExperimentTerminated):
                        with owned_session([sys.executable, '-c', TREE, str(pids), 'wait', CHILD, 'same-group'],
                                           stdout=subprocess.DEVNULL, term_timeout=.2, kill_timeout=2):
                            self.fail('pending TERM must be delivered before the body')
                records = json.loads(pids.read_text())
                self.write_evidence({'process_states': self.assert_stopped(records), 'registration_term_replayed': True})
            finally:
                if pids.exists(): cleanup_records(json.loads(pids.read_text()))

    def test_registration_failure_cleans_child_in_different_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pids = Path(directory) / 'pids.json'

            def failed_registration(*args, **kwargs):
                wait_path(pids)
                raise RuntimeError('synthetic registration failure')

            try:
                with patch('experiments.processes.OwnedSession', side_effect=failed_registration):
                    with self.assertRaisesRegex(RuntimeError, 'synthetic registration failure'):
                        with owned_session([sys.executable, '-c', TREE, str(pids), 'wait', CHILD, 'new-group'],
                                           stdout=subprocess.DEVNULL):
                            self.fail('must not enter')
                records = json.loads(pids.read_text())
                deadline = time.monotonic() + 2
                while any(p['state'] not in ('absent', psutil.STATUS_ZOMBIE) for p in states(records)) and time.monotonic() < deadline:
                    time.sleep(.02)
                self.write_evidence({'process_states': self.assert_stopped(records), 'registration_failure_cleaned': True})
            finally:
                if pids.exists(): cleanup_records(json.loads(pids.read_text()))

    def test_real_term_unwinds_entrypoint_and_kills_resistant_child(self):
        driver = r'''
from contextlib import nullcontext
import os,subprocess,sys,time
from pathlib import Path
from unittest.mock import patch
from experiments.processes import owned_session
from experiments.run import main
def fake_case(out, info, settings):
    with owned_session([sys.executable, '-c', sys.argv[4], sys.argv[2], 'wait', sys.argv[5], 'same-group'],
                       stdout=subprocess.DEVNULL):
        deadline=time.monotonic()+8
        while not Path(sys.argv[2]).exists():
            if time.monotonic()>deadline: raise RuntimeError('synthetic child not ready')
            time.sleep(.02)
        Path(sys.argv[3]).write_text('ready')
        time.sleep(60)
    return True
arguments=['experiment','--case','prefix','--out',sys.argv[1]]
with patch('experiments.run.require_cuda'), patch('experiments.run.stopped_services',return_value=nullcontext({})), \
     patch('experiments.prefix.run',fake_case):
    original_argv=sys.argv
    def invoke_case(out, info, settings):
        with patch('sys.argv',original_argv): return fake_case(out, info, settings)
    with patch('experiments.prefix.run',invoke_case), patch('sys.argv',arguments):
        raise SystemExit(main())
'''
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); pids = base / 'pids.json'; ready = base / 'ready'; out = base / 'out'
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
            env = os.environ.copy()
            for name in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET', 'DATA_DIR', 'CONTRACT_TEST'):
                env.pop(name, None)
            process = subprocess.Popen([sys.executable, '-c', driver, str(out), str(pids), str(ready), TREE, CHILD],
                                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                wait_path(ready)
                started = time.monotonic(); process.send_signal(signal.SIGTERM)
                time.sleep(.1); process.send_signal(signal.SIGTERM)
                output, _ = process.communicate(timeout=12)
                elapsed = time.monotonic() - started
                self.assertEqual(process.returncode, 1, output.decode(errors='replace'))
                self.assertLess(elapsed, 12)
                info = json.loads((out / 'manifest.json').read_text())
                failure = json.loads((out / 'failure.json').read_text())
                self.assertFalse(info['complete'])
                self.assertEqual(failure['type'], 'ExperimentTerminated')
                observed = self.assert_stopped(json.loads(pids.read_text()))
                self.assertIsNone(unrelated.poll())
                self.write_evidence({'process_states': observed, 'exit_code': process.returncode,
                                     'manifest_complete': info['complete'], 'failure_type': failure['type'],
                                     'unrelated_process_alive': True, 'elapsed_after_term_seconds': elapsed,
                                     'second_term_did_not_interrupt_cleanup': True})
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=3)
                if pids.exists(): cleanup_records(json.loads(pids.read_text()))
                unrelated.kill(); unrelated.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
