"""Standard-library entrypoint; bootstrap a dedicated, version-locked environment."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import venv
from server.locking import exclusive_lock

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--contract-test', action='store_true')
    group.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('Use Python 3.12 for the locked environment')
    with exclusive_lock(ROOT / '.runtime/setup.lock'):
        if args.stop:
            for name in ('.venv', '.venv-gpu'):
                python = ROOT / name / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
                if python.exists():
                    return subprocess.call([str(python), '-m', 'server.supervisor', '--stop'], cwd=ROOT)
            return 0
        if not args.contract_test:
            if sys.platform != 'linux':
                raise SystemExit('Live SGLang deployment requires the Linux GPU environment; use --contract-test locally')
            subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], check=True, timeout=10)
        path = ROOT / ('.venv' if args.contract_test else '.venv-gpu')
        python = path / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        lock = ROOT / ('requirements.lock' if args.contract_test else 'requirements-native-linux-py312.lock')
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        marker = path / '.dependencies-sha256'
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(path)
        if not marker.exists() or marker.read_text() != digest:
            subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(lock)], check=True)
            subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
            marker.write_text(digest)
        env = os.environ.copy()
        env['CONTRACT_TEST'] = '1' if args.contract_test else '0'
        if not args.contract_test:
            bench = ROOT / '.venv-bench'
            bench_python = bench / 'bin/python'
            bench_lock = ROOT / 'requirements-benchmark.lock'
            bench_digest = hashlib.sha256(bench_lock.read_bytes() + (ROOT / 'requirements.lock').read_bytes()).hexdigest()
            bench_marker = bench / '.dependencies-sha256'
            if not bench_python.exists():
                venv.EnvBuilder(with_pip=True).create(bench)
            if not bench_marker.exists() or bench_marker.read_text() != bench_digest:
                subprocess.run([str(bench_python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(bench_lock)], check=True)
                subprocess.run([str(bench_python), '-m', 'pip', 'check'], check=True)
                bench_marker.write_text(bench_digest)
        return subprocess.call([str(python), '-m', 'server.supervisor'], cwd=ROOT, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
