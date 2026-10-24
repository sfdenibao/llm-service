import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class LocalStack:
    def __init__(self, output, protocol=False):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.runtime = ROOT / '.runtime' / ('test-' + secrets.token_hex(5))
        self.runtime.mkdir(parents=True)
        self.data = self.runtime / 'data'
        self.port, self.engine_port = free_port(), free_port()
        self.base = f'http://127.0.0.1:{self.port}'
        self.fixture = f'http://127.0.0.1:{self.engine_port}'
        self.env = os.environ.copy()
        self.env.update(API_KEY=secrets.token_urlsafe(32), ADMIN_API_KEY=secrets.token_urlsafe(32),
                        KEY_HASH_SECRET=secrets.token_urlsafe(32), FIXTURE_CONTROL_KEY=secrets.token_urlsafe(32),
                        MODEL_ID='Qwen/Qwen2.5-7B-Instruct', MODEL_REVISION='a09a35458c702b33eeacc393d103063234e8bc28',
                        BENCH_PROFILE='optimized', DATA_DIR=str(self.data), HOST='127.0.0.1', PORT=str(self.port),
                        ENGINE_BASE_URL=self.fixture, FIXTURE_PORT=str(self.engine_port), FIXTURE_MODEL='Qwen/Qwen2.5-7B-Instruct',
                        CONTRACT_TEST='1', GATEWAY_SLOTS='2', GATEWAY_QUEUE='2', QUEUE_TIMEOUT_SECONDS='1',
                        EXECUTION_TIMEOUT_SECONDS='2', CUDA_VISIBLE_DEVICES='', PYTHONUNBUFFERED='1')
        self.secrets = [self.env[k] for k in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET', 'FIXTURE_CONTROL_KEY')]
        self.fixture_process = None
        self.fixture_script = ROOT / ('tests/protocol_fixture.py' if protocol else 'tools/upstream/credential_fixture.py')

    def start(self, setup=True):
        with (self.runtime / 'fixture.log').open('ab') as log:
            self.fixture_process = subprocess.Popen([sys.executable, str(self.fixture_script)],
                cwd=ROOT, env=self.env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.wait(self.fixture + '/health')
        self.start_gateway(setup)

    def wait(self, url, timeout=15):
        end=time.monotonic()+timeout
        with httpx.Client(timeout=1,trust_env=False) as client:
            while time.monotonic()<end:
                try:
                    if client.get(url).status_code==200:return
                except httpx.HTTPError:pass
                time.sleep(.1)
        raise RuntimeError('Test service readiness timeout')

    def start_gateway(self, setup=False):
        cmd=[sys.executable,'-m','server.bootstrap','--contract-test'] if setup else [sys.executable,'-m','server.supervisor']
        process=subprocess.run(cmd,cwd=ROOT,env=self.env,capture_output=True,text=True,timeout=180)
        text=process.stdout+process.stderr
        for secret in self.secrets:text=text.replace(secret,'[REDACTED]')
        with (self.output/'setup-local.txt').open('a',encoding='utf-8') as f:f.write(text)
        if process.returncode:
            raise RuntimeError('Gateway setup failed; inspect preserved local logs')
        self.wait(self.base+'/healthz')

    def stop_gateway(self):
        result=subprocess.run([sys.executable,'-m','server.supervisor','--stop'],cwd=ROOT,env=self.env,capture_output=True,text=True,timeout=25)
        if result.returncode:raise RuntimeError('Owned test gateway cleanup failed')

    def stop(self):
        try:self.stop_gateway()
        finally:
            if self.fixture_process and self.fixture_process.poll() is None:
                self.fixture_process.terminate()
                try:self.fixture_process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.fixture_process.kill();self.fixture_process.wait()

    def payload(self, stream=False, **kw):
        return {'model':self.env['MODEL_ID'],'messages':[{'role':'user','content':'CPU protocol fixture'}],
                'temperature':0,'max_tokens':50,'stream':stream,**kw}

    def headers(self, key=None):return {'Authorization':'Bearer '+(key or self.env['API_KEY'])}

    async def configure(self, client, **kw):
        plan={'prompt_tokens':100,'completion_tokens':50,'delay':0.,'omit_usage':False,'fail_before':False,'pause_after_usage':0.}
        plan.update(kw)
        r=await client.post(self.fixture+'/__fixture/configure',headers=self.headers(self.env['FIXTURE_CONTROL_KEY']),json=plan)
        r.raise_for_status()

    async def state(self, client):
        r=await client.get(self.fixture+'/__fixture/state',headers=self.headers(self.env['FIXTURE_CONTROL_KEY']))
        r.raise_for_status();return r.json()

    async def create_user(self, client, balance=100000, tokens=100000, rpm=1000, concurrency=64):
        admin=self.headers(self.env['ADMIN_API_KEY'])
        r=await client.post(self.base+'/admin/tenants',headers=admin,json={'name':'CPU '+secrets.token_hex(4),'balance_micro_usd':balance,'token_limit':tokens,'rpm_limit':rpm,'concurrency_limit':concurrency})
        r.raise_for_status();tenant=r.json()
        r=await client.post(self.base+'/admin/tenants/'+tenant['tenant_id']+'/keys',headers=admin,json={'name':'test'})
        r.raise_for_status();key=r.json();self.secrets.append(key['api_key'])
        return tenant,key
