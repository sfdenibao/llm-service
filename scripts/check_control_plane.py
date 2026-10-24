"""Public HTTP-only checks against your own already-running service. No official score."""
import argparse
import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import urlsplit
import httpx


async def check(base, suite):
    parts=urlsplit(base)
    if parts.scheme!='http' or not ipaddress.ip_address(parts.hostname).is_loopback or not parts.port or parts.path or parts.username:
        raise ValueError('Use an explicit loopback HTTP origin; tunnel remote services first')
    admin=os.environ['ADMIN_API_KEY'];api_key=os.environ['API_KEY']
    started=datetime.now(timezone.utc).isoformat();checks=[];created=[];price=None
    async with httpx.AsyncClient(base_url=base,timeout=45,trust_env=False) as client:
        async def req(method,path,key=admin,data=None,expected=200,extra=None):
            r=await client.request(method,path,headers={'Authorization':'Bearer '+key,**(extra or {})},json=data)
            if r.status_code!=expected:raise AssertionError('unexpected_http_status')
            return r.json()
        info=await req('GET','/info',key=api_key)
        if (suite=='mock')!=(info.get('mode')=='contract-test'):
            raise ValueError('Requested suite does not match actual service mode')
        model=info['model_id']
        payload={'model':model,'messages':[{'role':'user','content':'Reply exactly OK.'}],
                 'temperature':0,'max_tokens':50 if suite=='mock' else 64,'stream':False}
        async def record(test_id,expected,fn):
            try:
                actual=await fn();checks.append({'test_id':test_id,'expected':expected,'actual':actual,'passed':True,'evidence':{'transport':'HTTP','suite':suite}})
            except (AssertionError,httpx.HTTPError,ValueError,KeyError,TypeError):
                checks.append({'test_id':test_id,'expected':expected,'actual':'Assertion or transport failed; raw credentials/responses omitted','passed':False,'evidence':{'transport':'HTTP','suite':suite}})
        keys=[]
        try:
            price=await req('GET','/admin/pricing')
            await req('PUT','/admin/pricing',data={'model':model,'input_micro_usd_per_token':2,'output_micro_usd_per_token':6})
            for name in ('Alice','Bob'):
                t=await req('POST','/admin/tenants',data={'name':name+' selftest '+secrets.token_hex(3),'balance_micro_usd':10000000,'token_limit':10000000,'rpm_limit':1000,'concurrency_limit':32},expected=201)
                created.append(t['tenant_id'])
                group=[]
                for number in (1,2):
                    group.append(await req('POST',f'/admin/tenants/{t["tenant_id"]}/keys',data={'name':f'key {number}'},expected=201))
                keys.append(group)

            async def isolation():
                for group in keys:
                    for key in group:await req('POST','/v1/chat/completions',key['api_key'],payload)
                counts=[]
                for tid,group in zip(created,keys):
                    report=await req('GET','/v1/usage',group[0]['api_key'])
                    assert len(report['items'])==2 and all(r['tenant_id']==tid for r in report['items'])
                    counts.append(len(report['items']))
                await req('GET','/admin/tenants',keys[0][0]['api_key'],expected=401)
                await req('GET','/v1/usage?tenant_id='+created[1],keys[0][0]['api_key'],expected=403)
                return {'tenant_request_counts':counts,'cross_tenant_denied':True}
            await record('isolation','Two users, two keys each; separate bills; ordinary keys denied admin',isolation)

            async def billing():
                key=keys[0][0]['api_key'];before=(await req('GET','/v1/usage',key))['summary']['cost_micro_usd']
                answer=await req('POST','/v1/chat/completions',key,payload)
                async with client.stream('POST','/v1/chat/completions',headers={'Authorization':'Bearer '+key},json={**payload,'stream':True}) as response:
                    assert response.status_code==200
                    text=(await response.aread()).decode()
                    assert '[DONE]' in text and '"error"' not in text
                report=await req('GET','/v1/usage',key);last=report['items'][-2:]
                assert len(last)==2 and all(r['cost_micro_usd']==r['input_tokens']*2+r['output_tokens']*6 for r in last)
                if suite=='mock':assert all(r['cost_micro_usd']==500 for r in last)
                assert report['summary']['cost_micro_usd']-before==sum(r['cost_micro_usd'] for r in last)
                assert report['summary']['reserved_micro_usd']==0
                return {'bills':len(last),'cost_micro_usd':[r['cost_micro_usd'] for r in last],'usage_without_client_request':True}
            await record('billing','Stream and nonstream use actual engine usage; exact integer settlement',billing)

            async def idempotency():
                key=keys[0][0]['api_key'];idem=secrets.token_hex(16)
                before=len((await req('GET','/v1/usage',key))['items'])
                async def call():return await client.post('/v1/chat/completions',headers={'Authorization':'Bearer '+key,'Idempotency-Key':idem},json=payload)
                responses=await asyncio.gather(*(call() for _ in range(20)))
                assert sum(r.status_code==200 for r in responses)==1
                assert all(r.status_code in (200,409) for r in responses)
                assert len((await req('GET','/v1/usage',key))['items'])==before+1
                await req('POST','/v1/chat/completions',key,{**payload,'max_tokens':51},expected=409,extra={'Idempotency-Key':idem})
                return {'attempts':20,'successful_generation':1,'new_ledger_rows':1}
            await record('idempotency','20 retries create one generation and one bill; changed body conflicts',idempotency)

            async def lifecycle():
                old=keys[1][0]
                new=await req('POST','/admin/keys/'+old['key_id']+'/rotate',data={})
                assert new['key_id']!=old['key_id']
                await req('GET','/v1/usage',old['api_key'],expected=401)
                await req('POST','/v1/chat/completions',new['api_key'],payload)
                await req('POST','/admin/keys/'+new['key_id']+'/revoke',data={})
                await req('GET','/v1/usage',new['api_key'],expected=401)
                listing=await req('GET',f'/admin/tenants/{created[1]}/keys')
                assert all('api_key' not in k and 'key_hash' not in k for k in listing['items'])
                return {'rotated':True,'old_and_revoked_rejected':True,'key_listing_masked':True}
            await record('key_lifecycle','Rotate and revoke take effect; no plaintext in key listing',lifecycle)

            async def versions():
                key=keys[1][1]['api_key'];await req('POST','/v1/chat/completions',key,payload)
                prior=(await req('GET','/v1/usage',key))['items'][-1]
                await req('PUT','/admin/pricing',data={'model':model,'input_micro_usd_per_token':4,'output_micro_usd_per_token':8})
                await req('POST','/v1/chat/completions',key,payload)
                report=await req('GET','/v1/usage',key);new=report['items'][-1]
                assert prior['pricing_version']!=new['pricing_version']
                assert next(r for r in report['items'] if r['request_id']==prior['request_id'])['cost_micro_usd']==prior['cost_micro_usd']
                assert new['cost_micro_usd']==new['input_tokens']*4+new['output_tokens']*8
                hdr={'Idempotency-Key':secrets.token_hex(16)};url=f'/admin/tenants/{created[1]}/credits'
                a=await req('POST',url,data={'delta_micro_usd':1000,'reason':'selftest'},extra=hdr)
                b=await req('POST',url,data={'delta_micro_usd':1000,'reason':'selftest'},extra=hdr)
                assert a['adjustment_id']==b['adjustment_id']
                await req('POST',url,data={'delta_micro_usd':2000,'reason':'selftest'},extra=hdr,expected=409)
                return {'immutable_prices':True,'credits_idempotent':True}
            await record('pricing_credits','Versioned prices and idempotent balance adjustment',versions)

            if suite=='live':
                async def agent():
                    key=keys[0][1]['api_key']
                    result=await req('POST','/agent/run',key,{'task':'Where is order A20417? Return order_id, status and eta_days.','tool_data':{'orders':{'A20417':{'status':'shipped','eta_days':3}}}})
                    assert result['answer']=={'order_id':'A20417','status':'shipped','eta_days':3}
                    assert result['usage']['model_calls']>=2
                    return {'model_calls':result['usage']['model_calls'],'answer_matches_tool':True}
                await record('live_agent','Real model plan, lookup_order, model final answer',agent)
        finally:
            for tid in created:
                try:await req('PATCH','/admin/tenants/'+tid,data={'status':'disabled'})
                except Exception:pass
            if price:
                try:await req('PUT','/admin/pricing',data={k:price[k] for k in ('model','input_micro_usd_per_token','output_micro_usd_per_token')})
                except Exception:checks.append({'test_id':'restore_pricing','expected':'Restore original rates','actual':'Restore failed','passed':False,'evidence':{}})
    commit=subprocess.run(['git','rev-parse','--verify','HEAD'],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True)
    return {'schema':'credential-service-test/1','suite':suite,'commit_sha':commit.stdout.strip() or None,
            'started_at':started,'finished_at':datetime.now(timezone.utc).isoformat(),'checks':checks,
            'skipped':[{'test_id':x,'reason':'Requires the controlled local fault-injection suite scripts/run_tests.py'} for x in
                       ('raw_key_disk_scan','signal_gated_reservation','cancel_and_crash_recovery','shared_quota_limits')],
            'official_score':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base-url',required=True);p.add_argument('--suite',choices=('mock','live'),required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.out.exists():raise SystemExit('Choose a new output file')
    report=asyncio.run(check(a.base_url.rstrip('/'),a.suite))
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    passed=all(c['passed'] for c in report['checks'])
    print(json.dumps({'passed':passed,'checks':len(report['checks']),'skipped':len(report['skipped']),'official_score':False}))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
