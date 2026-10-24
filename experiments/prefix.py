"""Actual SGLang cache hits, with identical token IDs for cache-on/off comparisons."""
import json
import os
import subprocess
import time
from urllib.parse import urlsplit
import uuid
import httpx
import psutil
from experiments.common import append_row,json_hash,write_json
from server.supervisor import engine_command,port_free,wait_ready


def run(out,info,settings):
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer
    info.update(model=settings.model,revision=settings.revision,parameters={'prefix_tokens':512,'requests':8,'repeats':3,
        'dtype':'bfloat16','endpoint':'native SGLang /generate','purpose':'Mechanism evidence; not final gateway throughput'})
    tokenizer=AutoTokenizer.from_pretrained(settings.model,revision=settings.revision,local_files_only=True)
    start='<|im_start|>system\nFollow the final instruction exactly.<|im_end|>\n<|im_start|>user\n'
    context='The garden contains quiet paths, green trees and red flowers. '
    shared=tokenizer.encode(start+context*150,add_special_tokens=False)[:512]
    inputs={'shared':[],'unique':[]}
    for index in range(8):
        suffix=tokenizer.encode(f'\nRequest {index}. Ignore the context above. Reply exactly OK, without punctuation.<|im_end|>\n<|im_start|>assistant\n',add_special_tokens=False)
        unique=tokenizer.encode(start+f'Independent document {index}. '+context*150,add_special_tokens=False)[:512]
        inputs['shared'].append(shared+suffix);inputs['unique'].append(unique+suffix)
    write_json(out/'inputs.json',inputs)
    cache=snapshot_download(settings.model,revision=settings.revision,local_files_only=True)
    port=urlsplit(settings.engine_url).port
    overall=True;groups=[]
    for variant in ('off','on'):
        settings.config={**settings.config,'prefix_cache':variant=='on','max_running_requests':8,'max_total_tokens':16384}
        port_free('127.0.0.1',port)
        env={k:v for k,v in os.environ.items() if k not in ('API_KEY','ADMIN_API_KEY','KEY_HASH_SECRET','FIXTURE_CONTROL_KEY')}
        with (out/f'engine-{variant}.log').open('ab') as log:
            process=subprocess.Popen(engine_command(settings,cache,port),env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        try:
            wait_ready(settings.engine_url+'/health',process,180)
            with httpx.Client(base_url=settings.engine_url,timeout=30,trust_env=False) as client:
                for group in ('shared','unique'):
                    for repeat in range(3):
                        flush=client.post('/flush_cache')
                        write_json(out/f'{variant}-{group}-{repeat}-flush.json',{'status':flush.status_code})
                        if flush.status_code!=200:raise RuntimeError('Cache flush failed')
                        window=time.perf_counter();correct=successful=0
                        for index,ids in enumerate(inputs[group]):
                            t0=time.perf_counter();first=None;text='';meta={};done=False;transport=True
                            try:
                                with client.stream('POST','/generate',json={'rid':uuid.uuid4().hex,'input_ids':ids,'stream':True,
                                     'sampling_params':{'temperature':0,'max_new_tokens':8,'top_p':1.0,'top_k':-1,'repetition_penalty':1.0}}) as response:
                                    response.raise_for_status()
                                    for line in response.iter_lines():
                                        if not line.startswith('data:'):continue
                                        raw=line[5:].strip()
                                        if raw=='[DONE]':done=True;break
                                        value=json.loads(raw)
                                        if 'error' in value:raise ValueError('Engine error')
                                        content=value.get('text','')
                                        if content and first is None:first=time.perf_counter()
                                        text=content;meta=value.get('meta_info',{})
                                transport=done and meta.get('finish_reason') is not None
                            except (ValueError,httpx.HTTPError):transport=False
                            elapsed=(time.perf_counter()-t0)*1000;cached=meta.get('cached_tokens')
                            good=transport and text.strip()=='OK'
                            expected_cache=(type(cached) is int and ((cached==0) if variant=='off' else
                                (cached>=512 if group=='shared' and index>0 else cached<512)))
                            overall=overall and good and expected_cache
                            correct+=int(good);successful+=int(transport)
                            append_row(out/'raw.jsonl',{'variant':variant,'group':group,'repeat':repeat,'index':index,
                                'input_hash':json_hash(ids),'prefix_hash':json_hash(ids[:512]),'prefix_length':512,
                                'input_tokens':len(ids),'cached_tokens':cached,'cache_state':'hot' if index>0 and variant=='on' and group=='shared' else 'cold',
                                'output':text,'transport_ok':transport,'ttft_ms':(first-t0)*1000 if first else None,'e2e_ms':elapsed})
                        seconds=time.perf_counter()-window
                        groups.append({'variant':variant,'group':group,'repeat':repeat,'window_seconds':seconds,
                                       'success_rate':successful/8,'correct_rate':correct/8,'quality_goodput':correct/seconds,
                                       'goodput_definition':'Correct successful requests / window, no final-service SLO claim'})
        finally:
            p=psutil.Process(process.pid) if process.poll() is None else None
            owned=[p,*p.children(recursive=True)] if p else []
            for item in owned:
                try:item.terminate()
                except psutil.NoSuchProcess:pass
            _,alive=psutil.wait_procs(owned,timeout=10)
            for item in alive:
                try:item.kill()
                except psutil.NoSuchProcess:pass
            psutil.wait_procs(alive,timeout=5);process.wait(timeout=5)
    write_json(out/'summary.json',{'groups':groups,'cache_and_output_passed':overall})
    return overall
