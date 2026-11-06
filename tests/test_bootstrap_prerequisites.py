"""Exercise live build-prerequisite repair without installing host packages."""
import subprocess
import unittest
from unittest.mock import patch

from server.bootstrap import ensure_native_build_tools


class BootstrapPrerequisiteTests(unittest.TestCase):
    def test_existing_headers_and_compilers_do_not_install(self):
        with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
             patch('server.bootstrap.Path.is_file', return_value=True), \
             patch('server.bootstrap.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
             patch('server.bootstrap.subprocess.run') as run:
            ensure_native_build_tools()
        run.assert_not_called()

    def test_missing_prerequisites_install_only_needed_packages_and_recheck(self):
        for root, compiler_missing in ((True, False), (False, True)):
            with self.subTest(root=root, compiler_missing=compiler_missing):
                installed = False

                def which(name):
                    if compiler_missing and not installed and name in ('gcc', 'g++'):
                        return None
                    return '/usr/bin/' + name

                def execute(command, **kwargs):
                    nonlocal installed
                    if 'install' in command:
                        installed = True

                with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
                     patch('server.bootstrap.Path.is_file', side_effect=lambda: installed), \
                     patch('server.bootstrap.shutil.which', side_effect=which), \
                     patch('server.bootstrap.os.geteuid', return_value=0 if root else 1000, create=True), \
                     patch('server.bootstrap.subprocess.run', side_effect=execute) as run:
                    ensure_native_build_tools()
                self.assertEqual(run.call_count, 2)
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(commands[0][-1], 'update')
                self.assertIn('--no-install-recommends', commands[1])
                self.assertIn('python3.12-dev', commands[1])
                self.assertEqual('build-essential' in commands[1], compiler_missing)
                self.assertEqual(commands[1][:2] == ['sudo', '-n'], not root)
                self.assertTrue(all(call.kwargs['timeout'] <= 180 and call.kwargs['check']
                                    for call in run.call_args_list))

    def test_package_timeout_stops_before_installation_with_actionable_error(self):
        with patch('server.bootstrap.sysconfig.get_path', return_value='/usr/include/python3.12'), \
             patch('server.bootstrap.Path.is_file', return_value=False), \
             patch('server.bootstrap.shutil.which', side_effect=lambda name: '/usr/bin/' + name), \
             patch('server.bootstrap.os.geteuid', return_value=0, create=True), \
             patch('server.bootstrap.subprocess.run', side_effect=subprocess.TimeoutExpired('apt-get', 120)) as run:
            with self.assertRaisesRegex(SystemExit, 'package index update exceeded 120 seconds'):
                ensure_native_build_tools()
        self.assertEqual(run.call_count, 1)
