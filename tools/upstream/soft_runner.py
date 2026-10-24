"""CPU-only diagnostic runner for an ephemeral GitHub-hosted Linux VM.

Never run this entrypoint on a platform controller: it executes the submission's
setup.sh. No platform/cloud credential is required or forwarded to child code.
This is learner-owned diagnostic evidence, not a tamper-resistant grade.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submission', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted':
        raise SystemExit('Execute only in the provided GitHub-hosted CPU workflow, never on a controller/local host.')
    root = args.submission.resolve()
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    if not re.fullmatch(r'[a-f0-9]{40}', commit) or commit != os.environ.get('SOFT_COMMIT_SHA'):
        raise SystemExit('Checkout does not match the requested immutable commit.')
    repository = os.environ.get('GITHUB_REPOSITORY', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', repository):
        raise SystemExit('Invalid repository identity.')
    report = {'schema': 'llm-soft-check/1', 'project': 'project-2', 'mode': 'soft',
              'official_score': False, 'gpu_used': False, 'commit_sha': commit,
              'repository': repository, 'checks': [], 'status': 'error'}
    # Only ephemeral test secrets. Never expose Actions/cloud/platform credentials
    # to setup.sh or the gateway; checkout also disables credential persistence.
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LANG', 'TMPDIR') if k in os.environ}
    env.update(API_KEY=secrets.token_urlsafe(32), ADMIN_API_KEY=secrets.token_urlsafe(32),
               KEY_HASH_SECRET=secrets.token_urlsafe(32), FIXTURE_CONTROL_KEY=secrets.token_urlsafe(32),
               MODEL_ID='Qwen/Qwen2.5-7B-Instruct', MODEL_REVISION='a09a35458c702b33eeacc393d103063234e8bc28',
               BENCH_PROFILE='optimized', DATA_DIR=str(destination / 'data'), HOST='127.0.0.1', PORT='8000',
               ENGINE_BASE_URL='http://127.0.0.1:30001', FIXTURE_PORT='30001',
               FIXTURE_MODEL='Qwen/Qwen2.5-7B-Instruct', SOURCE_COMMIT=commit, GITHUB_REPOSITORY=repository,
               PYTHONUNBUFFERED='1', CUDA_VISIBLE_DEVICES='')
    for key in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET', 'FIXTURE_CONTROL_KEY'):
        print('::add-mask::' + env[key], flush=True)
    fixture = None
    started = time.monotonic()
    try:
        if not (root / 'setup.sh').is_file():
            report['checks'] = [{'id': 'setup', 'method': 'exec', 'path': 'setup.sh --contract-test',
                                 'status': 'missing', 'expected': 'Repository root contains setup.sh'}]
        else:
            fixture = subprocess.Popen([sys.executable, str(Path(__file__).with_name('credential_fixture.py'))], env=env)
            for _ in range(40):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:30001/health', timeout=1):
                        break
                except Exception:
                    time.sleep(.1)
            else:
                raise RuntimeError('CPU fixture did not become ready; no submission setup was executed')
            process = subprocess.Popen(['bash', 'setup.sh', '--contract-test'], cwd=root, env=env, start_new_session=True)
            try:
                code = process.wait(timeout=600)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise RuntimeError('setup.sh --contract-test exceeded the 10 minute CPU limit') from None
            if code != 0:
                raise RuntimeError('setup.sh --contract-test failed; inspect the Actions setup log')
            # The checker does not execute source. Its only target is the local
            # gateway wired to the independent CPU fixture started above.
            checker_env = dict(env)
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('soft_check.py')),
                                     '--base-url', 'http://127.0.0.1:8000', '--fixture-url', 'http://127.0.0.1:30001',
                                     '--out', str(destination / 'checks.json')], env=checker_env, timeout=180)
            if not (destination / 'checks.json').is_file():
                raise RuntimeError('CPU checker did not produce a report')
            checks = json.loads((destination / 'checks.json').read_text())
            report.update({k: checks[k] for k in ('checks', 'summary', 'notes', 'not_tested', 'limitations') if k in checks})
            report['status'] = 'passed' if result.returncode == 0 else 'failed'
    except Exception as error:
        # No submitted response bodies or credentials in the stored error.
        detail = str(error) if isinstance(error, RuntimeError) else 'CPU diagnostic interrupted; inspect Actions logs.'
        report['checks'].append({'id': 'setup_or_checker', 'method': 'exec', 'path': 'setup.sh --contract-test',
                                 'status': 'blocked', 'expected': detail})
    finally:
        try:
            subprocess.run(['bash', 'setup.sh', '--stop'], cwd=root, env=env, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass  # Ephemeral VM destruction is the final resource boundary.
        if fixture:
            fixture.terminate()
            try:
                fixture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.wait()
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    report['summary'] = {state: sum(c.get('status') == state for c in report['checks']) for state in ('pass', 'missing', 'invalid', 'blocked')}
    if report['summary']['missing'] or report['summary']['invalid'] or report['summary']['blocked']:
        report['status'] = 'failed'
    report['limitations'] = report.get('limitations', []) + ['CPU interface diagnostics only; no GPU inference or performance score.',
                            'Learner-controlled Actions evidence cannot replace the platform formal grader.']
    (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    # Render only known machine-generated fields, never arbitrary response HTML.
    lines = ['## Project 2 · CPU interface check', '', f'Commit: `{commit}`', '',
             'Diagnostic only · GPU not used · formal score unchanged', '', '| Check | Interface | Result |', '| --- | --- | --- |']
    for check in report['checks']:
        safe = lambda s: re.sub(r'[^A-Za-z0-9_ /{}.:=-]', '', str(s))[:180]
        lines.append(f"| {safe(check.get('id', ''))} | {safe(check.get('method', ''))} {safe(check.get('path', ''))} | {safe(check.get('status', ''))} |")
    summary = '\n'.join(lines) + '\n'
    (destination / 'summary.md').write_text(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write(summary)
    print(json.dumps({'status': report['status'], 'summary': report['summary'], 'commit_sha': commit}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
