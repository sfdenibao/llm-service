"""Bounded cleanup for the Linux session created by one mechanism experiment."""
from contextlib import contextmanager
import os
import signal
import subprocess
import sys
import time

import psutil


def _cleanup_unregistered(process):
    """Registration failed: retain the unreaped leader while draining its SID."""
    deadline = time.monotonic() + 4
    while True:
        live = []
        for pid in psutil.pids():
            try:
                if os.getsid(pid) != process.pid:
                    continue
                member = psutil.Process(pid)
                if member.status() != psutil.STATUS_ZOMBIE:
                    live.append(member)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                continue
        if not live:
            process.wait(timeout=.5)
            return
        for member in live:
            try:
                member.kill()
            except psutil.NoSuchProcess:
                pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Unregistered experiment session cleanup exceeded its deadline')
        time.sleep(.05)


class OwnedSession:
    """Track identity, not names or ports, including an exited leader's children."""

    def __init__(self, process, *, term_timeout=4.0, kill_timeout=4.0):
        if not (0 < term_timeout <= 4 and 0 < kill_timeout <= 4):
            raise ValueError('Cleanup phases must each be within (0, 4] seconds')
        self.process = process
        self.session_id = process.pid
        # Popen has not polled/reaped the leader yet, so even a short-lived
        # leader remains identifiable here. Children inherit this new session.
        leader = psutil.Process(process.pid)
        self.created = leader.create_time()
        if os.getsid(process.pid) != self.session_id:
            raise RuntimeError('Experiment child did not create its own session')
        self.known = {process.pid: self.created}
        self.term_timeout = term_timeout
        self.kill_timeout = kill_timeout

    @staticmethod
    def _identity(pid, created):
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - created) < .01 and process.status() != psutil.STATUS_ZOMBIE:
                return process
        except psutil.NoSuchProcess:
            pass
        return None

    def _live(self):
        # A new leader with the same numeric PID must never authorize signals.
        try:
            leader = psutil.Process(self.session_id)
            if abs(leader.create_time() - self.created) >= .01:
                raise RuntimeError('Experiment session identity was reused; refusing cleanup')
        except psutil.NoSuchProcess:
            pass
        for pid in psutil.pids():
            try:
                if os.getsid(pid) != self.session_id:
                    continue
                process = psutil.Process(pid)
                created = process.create_time()
                if created >= self.created - .01:
                    self.known[pid] = created
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
                continue
        live = {}
        for pid, created in list(self.known.items()):
            process = self._identity(pid, created)
            if process is None:
                continue
            live[pid] = process
            # Retain observed descendants even if they later change groups or
            # sessions. Session enumeration also finds already-orphaned peers.
            try:
                for child in process.children(recursive=True):
                    self.known[child.pid] = child.create_time()
                    if child.status() != psutil.STATUS_ZOMBIE:
                        live[child.pid] = child
            except psutil.NoSuchProcess:
                pass
        return live

    def _signal(self, processes, signum):
        for pid in processes:
            process = self._identity(pid, self.known[pid])
            if process is not None:
                try:
                    process.send_signal(signum)
                except psutil.NoSuchProcess:
                    pass

    def _drain(self, signum, seconds):
        deadline = time.monotonic() + seconds
        signalled = set()
        while True:
            live = self._live()
            if not live:
                return True
            # Include children forked while their parent was shutting down.
            pending = {pid: p for pid, p in live.items() if (pid, self.known[pid]) not in signalled}
            self._signal(pending, signum)
            signalled.update((pid, self.known[pid]) for pid in pending)
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(.05, max(0, deadline - time.monotonic())))

    def close(self):
        # A first TERM during normal cleanup is delivered after cleanup; a
        # second TERM while unwinding cannot interrupt TERM/KILL escalation.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            stopped = self._drain(signal.SIGTERM, self.term_timeout)
            if not stopped:
                stopped = self._drain(signal.SIGKILL, self.kill_timeout)
            if not stopped or self._live():
                raise RuntimeError('Experiment session cleanup did not finish within its deadline')
            self.process.wait(timeout=.5)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


@contextmanager
def owned_session(command, *, env=None, cwd=None, stdout=None,
                  term_timeout=4.0, kill_timeout=4.0):
    """Yield one Popen; always clean its session, including after parent exit."""
    if sys.platform != 'linux':
        raise RuntimeError('Mechanism subprocess sessions require Linux')
    if not (0 < term_timeout <= 4 and 0 < kill_timeout <= 4):
        raise ValueError('Cleanup phases must each be within (0, 4] seconds')
    previous_handler = signal.getsignal(signal.SIGTERM)
    pending = []
    restored = False

    def defer_term(signum, frame):
        pending.append((signum, frame))

    # Do not block TERM around Popen: the exec'd child would inherit that mask.
    # A Python handler instead defers only this parent's exception until the
    # owner and its finally are installed. Exec resets it in the child.
    signal.signal(signal.SIGTERM, defer_term)
    try:
        process = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stdout, start_new_session=True)
        try:
            owner = OwnedSession(process, term_timeout=term_timeout, kill_timeout=kill_timeout)
        except BaseException:
            # Popen has not polled/reaped this new session leader. Its PID
            # cannot be recycled while this direct child remains unreaped.
            _cleanup_unregistered(process)
            raise
        try:
            restored = True
            signal.signal(signal.SIGTERM, previous_handler)
            if pending and previous_handler != signal.SIG_IGN:
                if callable(previous_handler):
                    previous_handler(*pending[0])
                else:
                    raise SystemExit(128 + signal.SIGTERM)
            yield process
        finally:
            owner.close()
    finally:
        if not restored:
            signal.signal(signal.SIGTERM, previous_handler)
