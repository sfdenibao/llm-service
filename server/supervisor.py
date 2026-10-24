"""Only manage processes launched by this checkout; PID reuse is checked."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
import httpx
import psutil

from server.config import ROOT, Settings
from server.locking import exclusive_lock


def record(process):
    return {'pid': process.pid, 'create_time': process.create_time()}


def owned(value):
    try:
        p = psutil.Process(value['pid'])
        if abs(p.create_time() - value['create_time']) < .01 and p.status() != psutil.STATUS_ZOMBIE:
            return p
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return None


def save(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    tmp.replace(path)


def stop(data_dir):
    state_path = data_dir / 'processes.json'
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text())
    if state.get('project_root') != str(ROOT.resolve()):
        raise RuntimeError('Process manifest does not belong to this checkout')
    live = {}
    for item in state.get('processes', []):
        p = owned(item)
        if p:
            live[p.pid] = p
            for child in p.children(recursive=True):
                live[child.pid] = child
    for p in live.values():
        try:
            p.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(list(live.values()), timeout=10)
    for p in remaining:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    _, still_alive = psutil.wait_procs(remaining, timeout=5)
    if still_alive:
        raise RuntimeError('Owned process did not stop')
    state['processes'] = []
    save(state_path, state)
    (data_dir / 'gateway.pid').unlink(missing_ok=True)


def port_free(host, port):
    with socket.socket() as sock:
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError('Requested port is already in use; no unrelated process will be stopped')


def fingerprint(settings):
    values = {k: v for k, v in vars(settings).items() if k not in ('api_key', 'admin_key', 'pepper')}
    values['data_dir'] = str(values['data_dir'])
    values['secret_hashes'] = [hashlib.sha256(v.encode()).hexdigest() for v in (settings.api_key, settings.admin_key, settings.pepper)]
    values['source_hashes'] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for folder in ('server', 'control_plane', 'agent', 'migrations', 'configs')
                              for p in sorted((ROOT / folder).rglob('*')) if p.suffix in ('.py', '.sql', '.yaml', '.json')}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def wait_ready(url, process, timeout):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('Owned service exited; inspect its local log')
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
    raise RuntimeError('Service did not become ready before the startup deadline')


def engine_command(settings, model_path, port):
    c = settings.config
    command = [sys.executable, '-m', 'sglang.launch_server', '--model-path', str(model_path),
               '--served-model-name', settings.model, '--host', '127.0.0.1', '--port', str(port),
               '--dtype', c['dtype'], '--context-length', str(c['context_length']),
               '--max-running-requests', str(c['max_running_requests']), '--max-total-tokens', str(c['max_total_tokens']),
               '--mem-fraction-static', str(c['mem_fraction_static']), '--attention-backend', c['attention_backend'],
               '--sampling-defaults', 'openai', '--enable-cache-report', '--log-level', 'warning']
    if not c['prefix_cache']:
        command.append('--disable-radix-cache')
    return command


def start(settings):
    data_dir = settings.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / 'processes.json'
    wanted = fingerprint(settings)
    url = f'http://{settings.host}:{settings.port}/healthz'
    if state_path.exists():
        old = json.loads(state_path.read_text())
        if old.get('fingerprint') == wanted and old.get('processes') and all(owned(v) for v in old['processes'] if v.get('role') in ('gateway', 'engine')):
            try:
                with httpx.Client(timeout=3, trust_env=False) as client:
                    if client.get(url).status_code == 200:
                        print('Already ready; existing processes and ledger preserved')
                        return
            except httpx.HTTPError:
                pass
        stop(data_dir)
    port_free(settings.host, settings.port)
    state = {'project_root': str(ROOT.resolve()), 'fingerprint': wanted, 'processes': [], 'mode': 'contract-test' if settings.contract_test else 'live'}
    env = os.environ.copy()
    # Fixture-control credentials belong to the test driver and are never passed to the gateway.
    env.pop('FIXTURE_CONTROL_KEY', None)
    def spawn(command, role):
        path = data_dir / (role + '.log')
        child_env = env.copy()
        if role == 'engine':
            for key in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET'):
                child_env.pop(key, None)
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with path.open('ab') as log:
            child = subprocess.Popen(command, cwd=ROOT, env=child_env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=os.name != 'nt', creationflags=flags)
        state['processes'].append({**record(psutil.Process(child.pid)), 'role': role})
        save(state_path, state)
        return child
    try:
        if not settings.contract_test:
            from huggingface_hub import snapshot_download
            cache = snapshot_download(settings.model, revision=settings.revision)
            snapshot_download('Qwen/Qwen2.5-1.5B-Instruct', revision='989aa7980e4cf806f80c7fef2b1adb7bc71aa306')
            engine_port = urlsplit(settings.engine_url).port
            port_free('127.0.0.1', engine_port)
            command = engine_command(settings, cache, engine_port)
            engine = spawn(command, 'engine')
            wait_ready(settings.engine_url + '/health', engine, 750)
        gateway = spawn([sys.executable, '-m', 'server.main'], 'gateway')
        wait_ready(url, gateway, 90)
        actual_pid = int((data_dir / 'gateway.pid').read_text())
        actual = psutil.Process(actual_pid)
        if not any(c.status == psutil.CONN_LISTEN and c.laddr.port == settings.port for c in actual.net_connections(kind='inet')):
            raise RuntimeError('gateway.pid is not the listening process')
        for item in list(state['processes']):
            p = owned(item)
            if p:
                for child in p.children(recursive=True):
                    state['processes'].append({**record(child), 'role': 'child'})
        save(state_path, state)
        print(f'Ready: {url}; mode={state["mode"]}; gateway_pid={actual_pid}')
    except BaseException:
        stop(data_dir)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    data_dir = Path(os.environ.get('DATA_DIR', ROOT / '.runtime/data')).resolve()
    with exclusive_lock(data_dir / 'supervisor.lock'):
        if args.stop:
            stop(data_dir)
            print('Owned service processes stopped; DATA_DIR preserved')
        else:
            start(Settings.load())


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print('Setup failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
