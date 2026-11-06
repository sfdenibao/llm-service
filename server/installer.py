"""Bounded, logged installs using only stdlib and the selected venv's pip."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import time


class SetupFailure(RuntimeError):
    pass


class BudgetExpired(SetupFailure):
    pass


class Budget:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds

    def remaining(self, maximum=None):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise BudgetExpired('Setup time budget exhausted')
        return min(value, maximum) if maximum is not None else value


def redact(text, env):
    # Include arbitrary configured credential names as well as the service's known ones.
    secrets = [value for name, value in env.items() if value and
               (any(part in name.upper() for part in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET', 'CREDENTIAL')))]
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(https?://)[^\s/@]+(?::[^\s/@]*)?@', r'\1[REDACTED]@', text)
    text = re.sub(r'(?i)([?&](?:token|key|signature|sig|password|credential|x-amz-[^=\s]+)=)[^\s&#]+', r'\1[REDACTED]', text)
    return re.sub(r'(?i)(authorization\s*:\s*bearer\s+)\S+', r'\1[REDACTED]', text)


def kill_owned_command(process):
    """Only the process group created by run_logged; never search by name/port."""
    if os.name == 'nt':
        if process.poll() is None:
            try:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=3)


@dataclass
class CommandResult:
    returncode: int
    output: str


def run_logged(command, *, cwd, env, budget, log_path):
    """Raw subprocess output stays in RAM; only redacted text reaches disk/stdout."""
    timeout = budget.remaining()
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps previous failed attempts intact.
    with log_path.open('x', encoding='utf-8') as log:
        log.write('Command: ' + redact(' '.join(map(str, command)), env) + '\n')
        log.flush()
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=os.name != 'nt',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        timed_out = interrupted = False
        try:
            output, _ = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            timed_out = isinstance(error, subprocess.TimeoutExpired)
            interrupted = not timed_out
            kill_owned_command(process)
            output, _ = process.communicate(timeout=3)
        text = redact(output.decode('utf-8', errors='replace'), env)
        log.write(text)
        log.write(f'\nExit code: {process.returncode}; budget_timeout={timed_out}\n')
    if interrupted:
        raise KeyboardInterrupt
    if timed_out:
        raise BudgetExpired('Setup command exceeded its remaining budget; see ' + str(log_path))
    return CommandResult(process.returncode, text)


def network_failure(output):
    lower = output.lower()
    # A dependency, build, auth or integrity failure must not become a retry loop.
    permanent = ('resolutionimpossible', 'conflicting dependencies', 'no matching distribution found',
                 'could not find a version that satisfies', 'certificate_verify_failed',
                 'hashes do not match', '401 client error', '403 client error',
                 'failed building wheel', 'subprocess-exited-with-error')
    if any(pattern in lower for pattern in permanent):
        return False
    return any(pattern in lower for pattern in (
        'readtimeouterror', 'read timed out', 'connecttimeouterror', 'connection timed out',
        'connectionreseterror', 'connection reset by peer', 'remotedisconnected',
        'incompleteread', 'connection broken', 'temporary failure in name resolution',
        'network is unreachable', '502 server error', '503 server error', '504 server error'))


def install_locked(python, lock, marker, digest, *, cwd, env, budget, logs,
                   attempts=2, socket_timeout=60, retry_delay=2):
    if attempts < 1 or attempts > 3 or socket_timeout <= 0:
        raise ValueError('Invalid bounded pip policy')
    marker = Path(marker)
    if marker.is_file() and marker.read_text() == digest:
        return
    # An old/stale marker must not survive a failed update.
    marker.unlink(missing_ok=True)
    command = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
               '--no-input', '--progress-bar', 'off', '--timeout', str(socket_timeout),
               '--retries', '2', '-r', str(lock)]
    for attempt in range(1, attempts + 1):
        print(f'Installing {Path(lock).name}: attempt {attempt}/{attempts}, socket timeout {socket_timeout}s', flush=True)
        log_path = Path(logs) / f'install-{attempt}.log'
        result = run_logged(command, cwd=cwd, env=env, budget=budget, log_path=log_path)
        if result.returncode == 0:
            checked = run_logged([str(python), '-m', 'pip', 'check', '--disable-pip-version-check'],
                cwd=cwd, env=env, budget=budget, log_path=Path(logs) / 'pip-check.log')
            if checked.returncode:
                print('\n'.join(checked.output.splitlines()[-12:]), flush=True)
                raise SetupFailure('pip check failed; marker withheld. See ' + str(Path(logs) / 'pip-check.log'))
            budget.remaining()
            temporary = marker.with_suffix('.tmp')
            temporary.write_text(digest)
            temporary.replace(marker)
            return
        print('\n'.join(result.output.splitlines()[-12:]), flush=True)
        if not network_failure(result.output):
            raise SetupFailure('pip installation failed without a retryable network error; see ' + str(log_path))
        if attempt == attempts:
            raise SetupFailure('pip network retries exhausted; see ' + str(log_path))
        remaining = budget.remaining()
        if remaining <= retry_delay:
            raise BudgetExpired('Insufficient setup budget for another pip attempt')
        print('Transient download failure; keeping the failed log and reusing pip cache before retry', flush=True)
        time.sleep(retry_delay)
