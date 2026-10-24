"""Owned disposable CPU preview. All credentials stay in ignored local files, outside DATA_DIR."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.support import LocalStack


def main():
    root=ROOT/'.runtime/preview'
    root.mkdir(parents=True,exist_ok=True)
    stack=LocalStack(root,protocol=True)
    try:
        stack.start()
        private=root/'control.json'
        private.write_text(json.dumps({'base_url':stack.base,'admin_key':stack.env['ADMIN_API_KEY'],
                                      'api_key':stack.env['API_KEY'],'fixture_url':stack.fixture,
                                      'fixture_key':stack.env['FIXTURE_CONTROL_KEY']}),encoding='utf-8')
        if os.name!='nt':private.chmod(0o600)
        with httpx.Client(base_url=stack.base,headers=stack.headers(stack.env['ADMIN_API_KEY']),trust_env=False) as client:
            for name in ('Alice','Bob'):
                t=client.post('/admin/tenants',json={'name':name,'balance_micro_usd':100000,'token_limit':100000,'rpm_limit':60,'concurrency_limit':4}).json()
                k=client.post('/admin/tenants/'+t['tenant_id']+'/keys',json={'name':name+' Agent'}).json()['api_key']
                for stream in (False,True):client.post('/v1/chat/completions',headers=stack.headers(k),json=stack.payload(stream))
        print(json.dumps({'preview_url':stack.base+'/console','private_control_file':str(private),'cpu_fixture':True}),flush=True)
        while not (root/'stop').exists():time.sleep(.3)
    finally:
        stack.stop()
        (root/'control.json').unlink(missing_ok=True)
        (root/'stop').unlink(missing_ok=True)


if __name__=='__main__':main()
