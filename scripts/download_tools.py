"""Download the complete public V5 benchmark batch, validate its manifest, never execute it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

EXPECTED={'selftest.py','serving_benchmark.py','measurement.py','acceptance.json','prose_workload.py',
          'prose-bank.txt','acceptance-v3.pending.json','acceptance-v4.pending.json','acceptance-v5.pending.json'}
BASE='https://agentist.org/lab/llm-lab/'


def fetch(name):
    url=BASE+name
    with urllib.request.urlopen(url,timeout=30) as response:
        if response.status!=200 or response.geturl()!=url:raise RuntimeError('Unexpected HTTP status or redirect')
        data=response.read(2000001)
        if len(data)>2000000:raise RuntimeError('Public resource too large')
        return data


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    raw=fetch('tools-manifest.json');m=json.loads(raw);hashes=m.get('files_sha256')
    if m.get('schema')!='llm-public-tools/1' or not isinstance(hashes,dict) or set(hashes)!=EXPECTED or any(not re.fullmatch('[a-f0-9]{64}',v) for v in hashes.values()):raise RuntimeError('Unexpected public manifest')
    (a.out/'tools-manifest.json').write_bytes(raw)
    for name in sorted(EXPECTED):
        data=fetch(name)
        if hashlib.sha256(data).hexdigest()!=hashes[name]:raise RuntimeError('Public resource hash mismatch: '+name)
        (a.out/name).write_bytes(data)
    config=json.loads((a.out/'acceptance.json').read_text(encoding='utf-8'))
    if (config.get('model_id')!='Qwen/Qwen2.5-7B-Instruct' or config.get('model_revision')!='a09a35458c702b33eeacc393d103063234e8bc28'
        or config.get('workload_version')!='typed-json-numbered-records-prose-exact-input/5'
        or config.get('workload_version')!=m.get('workload_version') or config.get('response_format')!={'type':'json_object'}):raise RuntimeError('Taskbook version mismatch')
    print(json.dumps({'directory':str(a.out.resolve()),'files_verified':len(EXPECTED),'version':config['version'],'inference_run':False}))


if __name__=='__main__':main()
