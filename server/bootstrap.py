"""Standard-library entrypoint; bootstrap a dedicated, version-locked environment."""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import uuid
from server.installer import Budget, BudgetExpired, SetupFailure, install_locked, run_logged
from server.locking import exclusive_lock

ROOT = Path(__file__).resolve().parents[1]


def ensure_native_build_tools(budget=None, *, logs=None, env=None):
    """Triton compiles a Python extension at startup, even with cached wheels."""
    include = Path(sysconfig.get_path('include'))

    def missing():
        packages = []
        if not (include / 'Python.h').is_file():
            packages.append('python3.12-dev')
        if not shutil.which('gcc') or not shutil.which('g++'):
            packages.append('build-essential')
        return packages

    packages = missing()
    if not packages:
        return
    if not shutil.which('apt-get'):
        raise SystemExit('Live GPU setup needs ' + ', '.join(packages) +
                         '; apt-get is unavailable. Python headers must be in ' + str(include))
    timeout_program = shutil.which('timeout')
    if not timeout_program:
        raise SetupFailure('Live GPU prerequisites need GNU timeout for bounded privileged installation')
    prefix = []
    if os.geteuid() != 0:
        if not shutil.which('sudo'):
            raise SystemExit('Live GPU setup needs ' + ', '.join(packages) +
                             '; install these packages as root or provide passwordless sudo')
        prefix = ['sudo', '-n']
    budget = budget if budget is not None else Budget(300)
    env = env if env is not None else os.environ.copy()
    logs = Path(logs) if logs is not None else ROOT / '.runtime' / ('prerequisites-' + uuid.uuid4().hex[:8])
    print('Installing live GPU build prerequisites: ' + ', '.join(packages), flush=True)
    for operation, arguments, maximum, filename in (
        ('package index update', ['update'], 120, 'apt-update.log'),
        ('build prerequisite installation', ['install', '-y', '--no-install-recommends', *packages], 180, 'apt-install.log'),
    ):
        stage = Budget(budget.remaining(maximum))
        # The privileged guardian must send TERM/KILL before the unprivileged
        # outer process expires: killing sudo alone cannot kill root descendants.
        duration = int(stage.remaining() - 8)
        if duration < 1:
            raise BudgetExpired('Insufficient setup budget for privileged prerequisite cleanup')
        command = prefix + [timeout_program, '--signal=TERM', '--kill-after=5s', f'{duration}s',
                            'env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', *arguments]
        log_path = logs / filename
        try:
            result = run_logged(command, cwd=ROOT, env=env, budget=stage, log_path=log_path)
        except BudgetExpired:
            raise SetupFailure(f'Live GPU {operation} exceeded its outer deadline; privileged cleanup could not be confirmed. See {log_path}') from None
        if result.returncode:
            print('\n'.join(result.output.splitlines()[-12:]), flush=True)
            if result.returncode in (124, 137, -9):
                raise SetupFailure(f'Live GPU {operation} exceeded {duration} seconds under privileged timeout; see {log_path}')
            raise SetupFailure(f'Live GPU {operation} failed; ensure root or passwordless sudo access. See {log_path}')
    remaining = missing()
    if remaining:
        raise SystemExit('Live GPU build prerequisites still missing after installation: ' + ', '.join(remaining) +
                         '. Active Python include directory: ' + str(include))


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--contract-test', action='store_true')
    group.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('Use Python 3.12 for the locked environment')
    try:
        seconds = float(os.environ.get('SETUP_TIMEOUT_SECONDS', '840'))
        if not 60 <= seconds <= 900:
            raise ValueError()
    except ValueError:
        raise SystemExit('SETUP_TIMEOUT_SECONDS must be between 60 and 900') from None
    total = Budget(seconds)
    # The same deadline covers lock waits, prerequisites, both environments and readiness.
    # Reserve 30 seconds for command termination and detached-service manifest cleanup.
    work = Budget(seconds - 30)
    logs = ROOT / '.runtime' / ('setup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    env = os.environ.copy()
    with exclusive_lock(ROOT / '.runtime/setup.lock', timeout=work.remaining(120)):
        if args.stop:
            for name in ('.venv', '.venv-gpu'):
                python = ROOT / name / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                if python.exists():
                    stopped = run_logged([str(python), '-m', 'server.supervisor', '--stop'],
                        cwd=ROOT, env=env, budget=Budget(20), log_path=logs / 'stop.log')
                    print(stopped.output, end='')
                    return stopped.returncode
            return 0
        if not args.contract_test:
            if sys.platform != 'linux':
                raise SystemExit('Live SGLang deployment requires the Linux GPU environment; use --contract-test locally')
            subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], check=True, timeout=work.remaining(10))
            ensure_native_build_tools(work, logs=logs / 'prerequisites', env=env)
        path = ROOT / ('.venv' if args.contract_test else '.venv-gpu')
        python = path / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        lock = ROOT / ('requirements.lock' if args.contract_test else 'requirements-native-linux-py312.lock')
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        marker = path / '.dependencies-sha256'
        if not python.exists():
            created = run_logged([sys.executable, '-m', 'venv', str(path)], cwd=ROOT, env=env,
                                 budget=work, log_path=logs / 'create-service-venv.log')
            if created.returncode:
                raise SetupFailure('Cannot create isolated service environment; see ' + str(logs))
        install_locked(python, lock, marker, digest, cwd=ROOT, env=env, budget=work, logs=logs / 'service')
        env['PATH'] = str(python.parent) + os.pathsep + env.get('PATH', '')
        env['CONTRACT_TEST'] = '1' if args.contract_test else '0'
        if not args.contract_test:
            bench = ROOT / '.venv-bench'
            bench_python = bench / 'bin/python'
            bench_lock = ROOT / 'requirements-benchmark.lock'
            bench_digest = hashlib.sha256(bench_lock.read_bytes() + (ROOT / 'requirements.lock').read_bytes()).hexdigest()
            bench_marker = bench / '.dependencies-sha256'
            if not bench_python.exists():
                created = run_logged([sys.executable, '-m', 'venv', str(bench)], cwd=ROOT, env=env,
                                     budget=work, log_path=logs / 'create-benchmark-venv.log')
                if created.returncode:
                    raise SetupFailure('Cannot create isolated benchmark environment; see ' + str(logs))
            install_locked(bench_python, bench_lock, bench_marker, bench_digest,
                           cwd=ROOT, env=env, budget=work, logs=logs / 'benchmark')
        try:
            print(f'Starting supervised services; {work.remaining():.0f}s of work budget remains', flush=True)
            started = run_logged([str(python), '-m', 'server.supervisor'], cwd=ROOT, env=env,
                                 budget=work, log_path=logs / 'supervisor.log')
            print(started.output, end='')
            if started.returncode:
                raise SetupFailure('Supervised startup failed; see ' + str(logs / 'supervisor.log'))
            return 0
        except BaseException:
            # Engines and gateways start their own sessions. Killing the supervisor alone
            # cannot clean them up; its manifest validates root, PID and creation time.
            print('Startup failed or timed out; stopping owned services', flush=True)
            try:
                stopped = run_logged([str(python), '-m', 'server.supervisor', '--stop'],
                    cwd=ROOT, env=env, budget=Budget(total.remaining(20)), log_path=logs / 'cleanup.log')
                if stopped.returncode:
                    raise SetupFailure('Owned cleanup failed; see ' + str(logs / 'cleanup.log'))
            except (SetupFailure, OSError) as cleanup_error:
                raise SetupFailure('Startup failed and owned-process cleanup could not be confirmed; ' + str(cleanup_error)) from None
            raise


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (SetupFailure, OSError, subprocess.SubprocessError) as error:
        print('Setup failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
