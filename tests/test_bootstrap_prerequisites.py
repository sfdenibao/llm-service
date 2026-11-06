"""Exercise prerequisite repair and timeout placement without installing packages."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from server.bootstrap import ensure_native_build_tools
from server.installer import Budget, CommandResult, SetupFailure, run_logged


class BootstrapPrerequisiteTests(unittest.TestCase):
    def test_existing_headers_and_compilers_do_not_install(self):
        with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
             patch('server.bootstrap.Path.is_file', return_value=True), \
             patch('server.bootstrap.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
             patch('server.bootstrap.run_logged') as run:
            ensure_native_build_tools()
        run.assert_not_called()

    def test_missing_prerequisites_run_timeout_inside_root_or_sudo_and_recheck(self):
        for root, compiler_missing in ((True, False), (False, True)):
            with self.subTest(root=root, compiler_missing=compiler_missing):
                installed = False

                def which(name):
                    if compiler_missing and not installed and name in ('gcc', 'g++'):
                        return None
                    return '/usr/bin/' + name

                def execute(command, **kwargs):
                    nonlocal installed
                    timeout_pos = command.index('/usr/bin/timeout')
                    duration = float(command[timeout_pos + 3].removesuffix('s'))
                    self.assertLessEqual(duration + 7, kwargs['budget'].remaining())
                    if 'install' in command:
                        installed = True
                    return CommandResult(0, 'completed')

                with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
                     patch('server.bootstrap.Path.is_file', side_effect=lambda: installed), \
                     patch('server.bootstrap.shutil.which', side_effect=which), \
                     patch('server.bootstrap.os.geteuid', return_value=0 if root else 1000, create=True), \
                     patch('server.bootstrap.run_logged', side_effect=execute) as run:
                    ensure_native_build_tools(Budget(25), logs=Path('test-prerequisites'))
                self.assertEqual(run.call_count, 2)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(commands[0][-1], 'update')
                self.assertIn('--no-install-recommends', commands[1])
                self.assertIn('python3.12-dev', commands[1])
                self.assertEqual('build-essential' in commands[1], compiler_missing)
                self.assertEqual(commands[1][:2] == ['sudo', '-n'], not root)
                for command in commands:
                    start = command.index('/usr/bin/timeout')
                    self.assertEqual(start, 0 if root else 2)
                    self.assertEqual(command[start + 1:start + 3], ['--signal=TERM', '--kill-after=5s'])
                    self.assertEqual(command[start + 4:start + 7], ['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get'])
                self.assertEqual([call.kwargs['log_path'].name for call in run.call_args_list],
                                 ['apt-update.log', 'apt-install.log'])

    def test_privileged_timeout_or_insufficient_deadline_stops_before_install(self):
        for seconds, code, message, expected_calls in (
            (120, 124, 'package index update exceeded', 1),
            (120, 137, 'package index update exceeded', 1),
            (7, 0, 'Insufficient setup budget', 0),
        ):
            with self.subTest(seconds=seconds, code=code):
                with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
                     patch('server.bootstrap.Path.is_file', return_value=False), \
                     patch('server.bootstrap.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
                     patch('server.bootstrap.os.geteuid', return_value=0, create=True), \
                     patch('server.bootstrap.run_logged', return_value=CommandResult(code, 'guardian ended command')) as run:
                    with self.assertRaisesRegex(SetupFailure, message):
                        ensure_native_build_tools(Budget(seconds))
                self.assertEqual(run.call_count, expected_calls)

    @unittest.skipUnless(sys.platform == 'linux' and shutil.which('timeout'), 'Requires Linux GNU timeout')
    def test_real_timeout_kills_term_ignoring_sleep_subtree(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            pids = directory / 'pids.json'
            child_code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
            parent_code = ('import json,os,signal,subprocess,sys,time; '
                           'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                           'child=subprocess.Popen([sys.executable,"-c",sys.argv[2]]); '
                           'open(sys.argv[1],"w").write(json.dumps([os.getpid(),child.pid])); time.sleep(60)')
            evidence = Path(os.environ['TEST_RESULTS']) / 'prerequisite-timeout-subtree' if os.environ.get('TEST_RESULTS') else directory / 'evidence'
            evidence.mkdir(parents=True, exist_ok=False)
            started = time.monotonic()
            result = run_logged([shutil.which('timeout'), '--signal=TERM', '--kill-after=0.3s', '0.3s',
                sys.executable, '-c', parent_code, str(pids), child_code], cwd=directory,
                env=os.environ.copy(), budget=Budget(5), log_path=evidence / 'timeout.log')
            states = []
            for pid in json.loads(pids.read_text()):
                stat = Path(f'/proc/{pid}/stat')
                deadline = time.monotonic() + .5
                while True:
                    try:
                        status = stat.read_text().rsplit(')', 1)[1].split()[0]
                    except FileNotFoundError:
                        status = 'absent'
                    if status in ('absent', 'Z') or time.monotonic() >= deadline:
                        break
                    time.sleep(.01)
                states.append({'pid': pid, 'state': status})
                if status not in ('absent', 'Z'):
                    os.kill(pid, signal.SIGKILL)  # Only this test's recorded child, on failure.
            report = {'passed': result.returncode in (-9, 137) and all(item['state'] in ('absent', 'Z') for item in states),
                      'exit_code': result.returncode, 'elapsed_seconds': time.monotonic() - started,
                      'uid': os.geteuid(), 'process_states': states,
                      'scope': 'Real GNU timeout TERM then KILL of a synthetic sleep subtree; no apt or sudo execution'}
            (evidence / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            self.assertTrue(report['passed'], report)
            self.assertLess(report['elapsed_seconds'], 5)


if __name__ == '__main__':
    unittest.main()
