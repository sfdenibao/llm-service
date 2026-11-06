"""Checkout ownership and lifecycle locks, using isolated directories/processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psutil
from server import supervisor


class SupervisorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'checkout'
        self.root.mkdir()
        self.processes = []
        self.root_patch = patch.object(supervisor, 'ROOT', self.root)
        self.root_patch.start()
        self.env_patch = patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != 'DATA_DIR'}, clear=True)
        self.env_patch.start()

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.env_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def sleeper(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.processes.append(process)
        return process

    def manifest(self, data_dir, process, *, project_root=None, creation_offset=0):
        data_dir.mkdir(parents=True)
        value = supervisor.record(psutil.Process(process.pid))
        value['create_time'] += creation_offset
        value['role'] = 'gateway'
        state = {'project_root': str(project_root or self.root), 'processes': [value]}
        supervisor.save(data_dir / 'processes.json', state)
        (data_dir / 'gateway.pid').write_text(str(process.pid))
        (data_dir / 'ledger.db').write_bytes(b'preserve-existing-ledger')
        return state

    def seed_registry(self, directories, cache=None):
        value = supervisor.load_registry()
        value['data_dirs'] = [str(p) for p in directories]
        value['live_cache_env'] = cache or {}
        supervisor.save_registry(value)

    def probe_checkout_lock(self):
        # A different process must observe this lock on both Windows and Linux.
        program = (
            'import sys\nfrom pathlib import Path\nfrom server.locking import exclusive_lock\n'
            'try:\n with exclusive_lock(Path(sys.argv[1]), timeout=.15): print("acquired")\n'
            'except (RuntimeError, OSError):\n print("blocked"); sys.exit(42)\n')
        return subprocess.run([sys.executable, '-c', program, str(self.root / '.runtime/lifecycle.lock')],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def test_start_registers_before_work_and_discards_missing_manifest_entries(self):
        old = self.base / 'deleted-state'
        target = self.base / 'custom-state'
        self.seed_registry([old], {'HF_HOME': str(self.base / 'model-cache')})

        def startup(settings):
            value = supervisor.load_registry()
            self.assertEqual(value['data_dirs'], [str(target)])
            self.assertEqual(value['live_cache_env'], {'HF_HOME': str(self.base / 'model-cache')})
            self.assertEqual(self.probe_checkout_lock().returncode, 42)
            raise RuntimeError('simulated startup failure before spawn')

        with patch.object(supervisor, 'start_locked', side_effect=startup):
            with self.assertRaisesRegex(RuntimeError, 'simulated startup failure'):
                supervisor.start(SimpleNamespace(data_dir=target))
        self.assertFalse(old.exists())
        self.assertEqual(self.probe_checkout_lock().returncode, 0)

    def test_registry_write_failure_prevents_starting_children(self):
        with patch.object(supervisor, 'save_registry', side_effect=OSError('registry not writable')), \
             patch.object(supervisor, 'start_locked') as start:
            with self.assertRaisesRegex(OSError, 'registry not writable'):
                supervisor.start(SimpleNamespace(data_dir=self.base / 'custom-state'))
        start.assert_not_called()

    def test_explicit_stop_targets_one_directory_then_context_stops_all_and_preserves_data(self):
        first, second, legacy, external = [self.sleeper() for _ in range(4)]
        a, b = self.base / 'state-a', self.base / 'state-b'
        default = self.root / '.runtime/data'
        unknown = self.base / 'unregistered-fixture'
        for path, process in ((a, first), (b, second), (default, legacy), (unknown, external)):
            self.manifest(path, process)
        missing = self.base / 'removed-state'
        cache = {'HF_HOME': str(self.base / 'cache'), 'HF_HUB_CACHE': str(self.base / 'cache/hub')}
        self.seed_registry([a, b, missing], cache)

        with patch.dict(os.environ, {'DATA_DIR': str(a)}), patch.object(sys, 'argv', ['supervisor', '--stop']):
            supervisor.main()
        self.assertIsNotNone(first.poll())
        self.assertIsNone(second.poll())
        self.assertIsNone(legacy.poll())
        with supervisor.stopped_services() as restored_cache:
            self.assertEqual(restored_cache, cache)
            self.assertIsNotNone(second.poll())
            self.assertIsNotNone(legacy.poll())
            self.assertIsNone(external.poll())
            self.assertEqual(self.probe_checkout_lock().returncode, 42)
        self.assertEqual(self.probe_checkout_lock().returncode, 0)
        self.assertEqual(supervisor.load_registry()['live_cache_env'], cache)
        self.assertFalse(missing.exists())
        for path in (a, b, default):
            self.assertEqual((path / 'ledger.db').read_bytes(), b'preserve-existing-ledger')
            self.assertEqual(json.loads((path / 'processes.json').read_text())['processes'], [])
            self.assertFalse((path / 'gateway.pid').exists())
        self.assertIsNone(external.poll())

    def test_context_includes_explicit_unregistered_legacy_directory(self):
        process = self.sleeper()
        data_dir = self.base / 'legacy-explicit-state'
        self.manifest(data_dir, process)
        with patch.dict(os.environ, {'DATA_DIR': str(data_dir)}), supervisor.stopped_services() as cache:
            self.assertEqual(cache, {})
            self.assertIsNotNone(process.poll())

    def test_foreign_manifest_and_mismatched_pid_generation_are_not_killed(self):
        process = self.sleeper()
        foreign = self.base / 'foreign-state'
        original = self.manifest(foreign, process, project_root=self.base / 'other-project')
        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
            supervisor.stop(foreign)
        self.assertIsNone(process.poll())
        self.assertEqual(json.loads((foreign / 'processes.json').read_text()), original)
        reused = self.base / 'stale-pid-state'
        self.manifest(reused, process, creation_offset=-10)
        supervisor.stop(reused)
        self.assertIsNone(process.poll())
        self.assertEqual(json.loads((reused / 'processes.json').read_text())['processes'], [])

    def test_foreign_registry_fails_closed_and_experiment_failure_releases_global_lock(self):
        value = supervisor.load_registry()
        value['project_root'] = str(self.base / 'other-checkout')
        supervisor.save_registry(value)
        with self.assertRaisesRegex(RuntimeError, 'does not belong'):
            with supervisor.stopped_services():
                self.fail('Foreign registry must not yield')
        self.assertEqual(self.probe_checkout_lock().returncode, 0)
        value['project_root'] = str(self.root)
        supervisor.save_registry(value)
        with self.assertRaisesRegex(RuntimeError, 'experiment failed'):
            with supervisor.stopped_services():
                raise RuntimeError('experiment failed')
        self.assertEqual(self.probe_checkout_lock().returncode, 0)

    def test_cache_context_records_resolved_paths_without_credentials(self):
        cache = self.base / 'known-model-cache'
        supervisor.remember_live_cache(SimpleNamespace(HF_HOME=str(cache), HF_HUB_CACHE=str(cache / 'hub')))
        with supervisor.stopped_services() as restored:
            self.assertEqual(restored, {'HF_HOME': str(cache), 'HF_HUB_CACHE': str(cache / 'hub')})
        value = supervisor.load_registry()
        self.assertEqual(set(value), {'schema', 'project_root', 'data_dirs', 'live_cache_env'})
        self.assertEqual(set(value['live_cache_env']), {'HF_HOME', 'HF_HUB_CACHE'})


if __name__ == '__main__':
    unittest.main()
