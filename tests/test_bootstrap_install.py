"""Installer fault tests: real local wheel interruption, plus bounded failure paths."""
import base64
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import venv
import zipfile

from server.installer import Budget, BudgetExpired, CommandResult, SetupFailure, install_locked, run_logged


def probe_wheel():
    dist = 'installer_probe-0.0.1.dist-info/'
    files = {
        'installer_probe/__init__.py': b"RECOVERED = True\n",
        'installer_probe/padding.bin': b'x' * 131072,
        dist + 'METADATA': b'Metadata-Version: 2.1\nName: installer-probe\nVersion: 0.0.1\n',
        dist + 'WHEEL': b'Wheel-Version: 1.0\nGenerator: fault-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
    }
    record = io.StringIO(newline='')
    writer = csv.writer(record)
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip('=')
        writer.writerow((name, 'sha256=' + digest, len(content)))
    writer.writerow((dist + 'RECORD', '', ''))
    files[dist + 'RECORD'] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class BootstrapInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lock = self.root / 'requirements.lock'
        self.lock.write_text('installer-probe==0.0.1\n')
        self.marker = self.root / '.dependencies-sha256'
        self.logs = self.root / 'logs'
        self.env = os.environ.copy()

    def install(self, **kwargs):
        return install_locked(sys.executable, self.lock, self.marker, 'expected-digest',
            cwd=self.root, env=self.env, budget=kwargs.pop('budget', Budget(30)),
            logs=self.logs, retry_delay=kwargs.pop('retry_delay', 0), **kwargs)

    def test_real_pip_recovers_after_local_wheel_body_read_timeout(self):
        wheel = probe_wheel()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(wheel)))
                self.end_headers()
                if len(requests) == 1:
                    # Advertise the full wheel, then stall its body past pip's socket timeout.
                    self.wfile.write(wheel[:128])
                    self.wfile.flush()
                    time.sleep(1)
                else:
                    self.wfile.write(wheel)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        isolated = self.root / 'isolated'
        venv.EnvBuilder(with_pip=True).create(isolated)
        python = isolated / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        env = {key: value for key, value in self.env.items() if not key.startswith('PIP_')}
        env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX='1', PIP_NO_CACHE_DIR='1',
                   NO_PROXY='127.0.0.1', API_KEY='synthetic-private-test-key')
        self.lock.write_text('installer-probe @ http://127.0.0.1:' + str(server.server_port) +
                             '/installer_probe-0.0.1-py3-none-any.whl?token=synthetic-private-test-key\n')
        evidence = Path(os.environ['TEST_RESULTS']) / 'bootstrap-install-recovery' if os.environ.get('TEST_RESULTS') else self.logs
        evidence.mkdir(parents=True, exist_ok=False)
        logs = evidence / 'pip-logs'
        pip_version = subprocess.run([str(python), '-c',
            "from importlib.metadata import version; print(version('pip'))"],
            check=True, timeout=10, capture_output=True, text=True).stdout.strip()
        report = {'schema': 'local-pip-download-recovery/1', 'passed': False,
                  'transport': 'loopback HTTP only', 'pip_version': pip_version,
                  'socket_timeout_seconds': .2, 'outer_attempt_limit': 2,
                  'fault': 'First request advertises full Content-Length but stalls after 128 bytes for 1 second',
                  'first_timeout_observed': False, 'recovery_install_succeeded': False,
                  'pip_check_passed': False, 'marker_written': False, 'imported_version': None,
                  'not_tested': ['External CDN', '706 MB CUDA wheel', 'GPU cold startup', 'TLS/proxy recovery']}
        try:
            install_locked(python, self.lock, self.marker, 'expected-digest', cwd=self.root,
                env=env, budget=Budget(60), logs=logs, socket_timeout=.2, retry_delay=0)
            report['first_timeout_observed'] = 'ReadTimeoutError' in (logs / 'install-1.log').read_text()
            report['recovery_install_succeeded'] = 'Exit code: 0' in (logs / 'install-2.log').read_text()
            report['pip_check_passed'] = 'No broken requirements found' in (logs / 'pip-check.log').read_text()
            report['marker_written'] = self.marker.read_text() == 'expected-digest'
            imported = subprocess.run([str(python), '-c',
                "import installer_probe; from importlib.metadata import version; assert installer_probe.RECOVERED; print(version('installer-probe'))"],
                check=False, timeout=10, capture_output=True, text=True)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            report['imported_version'] = imported.stdout.strip()
            self.assertEqual(report['imported_version'], '0.0.1')
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(report[name] for name in ('first_timeout_observed', 'recovery_install_succeeded',
                                                         'pip_check_passed', 'marker_written')))
            self.assertNotIn('synthetic-private-test-key', '\n'.join(p.read_text() for p in logs.iterdir()))
            report['passed'] = True
        finally:
            report['http_requests'] = len(requests)
            (evidence / 'network-recovery.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def test_permanent_failure_stops_immediately_and_removes_stale_marker(self):
        self.marker.write_text('stale-digest')
        with patch('server.installer.run_logged', return_value=CommandResult(1,
                'ERROR: No matching distribution found; earlier ReadTimeoutError')) as run:
            with self.assertRaisesRegex(SetupFailure, 'without a retryable network error'):
                self.install()
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.marker.exists())

    def test_successful_install_with_failed_pip_check_cannot_write_marker(self):
        with patch('server.installer.run_logged', side_effect=[CommandResult(0, 'installed'),
                   CommandResult(1, 'dependency conflict')]) as run:
            with self.assertRaisesRegex(SetupFailure, 'pip check failed'):
                self.install()
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.marker.exists())

    def test_network_failure_stops_at_attempt_limit_without_marker(self):
        with patch('server.installer.run_logged', return_value=CommandResult(2, 'ReadTimeoutError')) as run:
            with self.assertRaisesRegex(SetupFailure, 'network retries exhausted'):
                self.install(attempts=2)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.marker.exists())

    def test_retry_cannot_extend_shared_budget(self):
        with patch('server.installer.run_logged', return_value=CommandResult(2, 'ReadTimeoutError')) as run:
            with self.assertRaisesRegex(BudgetExpired, 'Insufficient setup budget'):
                self.install(budget=Budget(.1), retry_delay=1)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.marker.exists())

    def test_command_deadline_terminates_child_and_preserves_redacted_failure_log(self):
        secret = 'synthetic-private-budget-key'
        self.env['API_KEY'] = secret
        log = self.logs / 'timeout.log'
        before = time.monotonic()
        with self.assertRaisesRegex(BudgetExpired, 'exceeded its remaining budget'):
            run_logged([sys.executable, '-c',
                "import os,time; print(os.environ['API_KEY'], flush=True); time.sleep(60)"],
                cwd=self.root, env=self.env, budget=Budget(.5), log_path=log)
        self.assertLess(time.monotonic() - before, 7)
        text = log.read_text()
        self.assertIn('budget_timeout=True', text)
        self.assertIn('[REDACTED]', text)
        self.assertNotIn(secret, text)

    def test_startup_deadline_invokes_manifest_cleanup(self):
        from server import bootstrap
        interpreter = self.root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        with patch.object(bootstrap, 'ROOT', self.root), \
             patch.object(bootstrap.sys, 'argv', ['bootstrap', '--contract-test']), \
             patch.object(bootstrap, 'install_locked'), \
             patch.object(bootstrap, 'run_logged', side_effect=[BudgetExpired('forced startup timeout'),
                          CommandResult(0, 'Owned service processes stopped')]) as run:
            with self.assertRaisesRegex(BudgetExpired, 'forced startup timeout'):
                bootstrap.main()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][-1], '--stop')
        self.assertEqual(run.call_args_list[1].kwargs['env'].get('DATA_DIR'), self.env.get('DATA_DIR'))


if __name__ == '__main__':
    unittest.main()
