import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import time
import unittest
import httpx
import psutil

from tests.support import LocalStack


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack=LocalStack(os.environ['TEST_RESULTS'],protocol=True)
        cls.stack.start()

    @classmethod
    def tearDownClass(cls):cls.stack.stop()

    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.s=self.stack
        self.c=httpx.AsyncClient(timeout=12,trust_env=False)
        self.admin=self.s.headers(self.s.env['ADMIN_API_KEY'])
        await self.s.configure(self.c)
        await self.mode('')
        await self.c.put(self.s.base+'/admin/pricing',headers=self.admin,json={'model':self.s.env['MODEL_ID'],'input_micro_usd_per_token':2,'output_micro_usd_per_token':6})
        self.tenant,self.key=await self.s.create_user(self.c)
        self.headers=self.s.headers(self.key['api_key'])

    async def asyncTearDown(self):
        await self.mode('')
        await self.c.aclose()

    async def mode(self,value):
        r=await self.c.post(self.s.fixture+'/__test/configure',headers=self.s.headers(self.s.env['FIXTURE_CONTROL_KEY']),json={'mode':value})
        self.assertEqual(r.status_code,200)

    async def release(self):
        await self.c.post(self.s.fixture+'/__test/release',headers=self.s.headers(self.s.env['FIXTURE_CONTROL_KEY']),json={})

    async def chat(self,headers=None,**payload):
        return await self.c.post(self.s.base+'/v1/chat/completions',headers=headers or self.headers,json=self.s.payload(**payload))

    async def usage(self,headers=None):
        r=await self.c.get(self.s.base+'/v1/usage',headers=headers or self.headers)
        self.assertEqual(r.status_code,200);return r.json()

    async def metrics(self):
        r=await self.c.get(self.s.base+'/metrics',headers=self.headers);return r.json()

    async def wait_until(self,probe,timeout=8):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            value=await probe()
            if value:return value
            await asyncio.sleep(.05)
        self.fail('Condition did not converge before deadline')

    async def second_key(self,tenant=None):
        tid=(tenant or self.tenant)['tenant_id']
        r=await self.c.post(self.s.base+'/admin/tenants/'+tid+'/keys',headers=self.admin,json={'name':'second'})
        self.assertEqual(r.status_code,201);key=r.json();self.s.secrets.append(key['api_key']);return key

    async def test_01_billing_stream_and_nonstream(self):
        a=await self.chat();b=await self.chat(stream=True)
        self.assertEqual((a.status_code,b.status_code),(200,200))
        self.assertIn('data: [DONE]',b.text);self.assertNotIn('"usage"',b.text)
        report=await self.usage();self.assertEqual(report['summary']['cost_micro_usd'],1000)
        self.assertEqual(report['summary']['reserved_micro_usd'],0)
        self.assertTrue(all(r['input_tokens']==100 and r['output_tokens']==50 for r in report['items']))

    async def test_02_two_tenants_four_keys_isolation(self):
        other,otherkey=await self.s.create_user(self.c)
        a2,b2=await self.second_key(),await self.second_key(other)
        keys=[self.key,a2,otherkey,b2]
        responses=await asyncio.gather(*(self.chat(headers=self.s.headers(k['api_key'])) for k in keys))
        self.assertTrue(all(r.status_code==200 for r in responses))
        a,b=await self.usage(),await self.usage(self.s.headers(otherkey['api_key']))
        self.assertEqual(len(a['items']),2);self.assertEqual(len(b['items']),2)
        self.assertTrue(all(r['tenant_id']==self.tenant['tenant_id'] for r in a['items']))
        r=await self.c.get(self.s.base+'/v1/usage?tenant_id='+other['tenant_id'],headers=self.headers)
        self.assertEqual(r.status_code,403)

    async def test_03_key_lifecycle_storage_and_admin_denial(self):
        await self.chat()
        r=await self.c.post(self.s.base+'/admin/keys/'+self.key['key_id']+'/rotate',headers=self.admin,json={})
        self.assertEqual(r.status_code,200);new=r.json();self.s.secrets.append(new['api_key'])
        self.assertTrue(new['key_id']!=self.key['key_id'] and new['api_key']!=self.key['api_key'])
        self.assertEqual((await self.chat()).status_code,401)
        self.assertEqual((await self.chat(headers=self.s.headers(new['api_key']))).status_code,200)
        usage=await self.usage(self.s.headers(new['api_key']));self.assertEqual(usage['items'][0]['key_id'],self.key['key_id'])
        denied=await self.c.post(self.s.base+'/admin/tenants',headers=self.s.headers(new['api_key']),json={})
        self.assertEqual(denied.status_code,401)
        for _ in range(2):
            self.assertEqual((await self.c.post(self.s.base+'/admin/keys/'+new['key_id']+'/revoke',headers=self.admin,json={})).status_code,200)
        self.assertEqual((await self.chat(headers=self.s.headers(new['api_key']))).status_code,401)
        past=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
        expired=await self.c.post(self.s.base+'/admin/tenants/'+self.tenant['tenant_id']+'/keys',headers=self.admin,json={'name':'expired','expires_at':past})
        self.assertEqual(expired.status_code,201);raw=expired.json()['api_key'];self.s.secrets.append(raw)
        self.assertEqual((await self.chat(headers=self.s.headers(raw))).status_code,401)
        for path in self.s.data.rglob('*'):
            if path.is_file():
                data=path.read_bytes();self.assertFalse(any(s.encode() in data for s in self.s.secrets),'Credential found on DATA_DIR disk')

    async def test_04_idempotency_twenty_concurrent(self):
        await self.mode('gate');key='retry-'+secrets.token_hex(8)
        headers={**self.headers,'Idempotency-Key':key}
        before=len((await self.s.state(self.c))['calls'])
        tasks=[asyncio.create_task(self.chat(headers=headers)) for _ in range(20)]
        async def all_but_one():return sum(t.done() for t in tasks)>=19
        await self.wait_until(all_but_one)
        self.assertEqual(sum(t.done() and t.result().status_code==409 for t in tasks),19)
        await self.release();results=await asyncio.gather(*tasks)
        self.assertEqual(sum(r.status_code==200 for r in results),1)
        self.assertEqual(len((await self.s.state(self.c))['calls'])-before,1)
        self.assertEqual((await self.usage())['summary']['cost_micro_usd'],500)
        changed=await self.chat(headers=headers,max_tokens=51);self.assertEqual(changed.status_code,409)
        again=await self.chat(headers=headers);self.assertEqual(again.status_code,409)

    async def test_05_atomic_reservation_twenty_concurrent(self):
        tenant,key=await self.s.create_user(self.c,balance=500)
        await self.mode('gate');headers=self.s.headers(key['api_key'])
        before=len((await self.s.state(self.c))['calls'])
        tasks=[asyncio.create_task(self.chat(headers=headers)) for _ in range(20)]
        async def all_but_one():return sum(t.done() for t in tasks)>=19
        await self.wait_until(all_but_one)
        self.assertEqual(sum(t.done() and t.result().status_code==402 for t in tasks),19)
        reserved=(await self.usage(headers))['summary'];self.assertEqual(reserved['reserved_micro_usd'],500)
        await self.release();responses=await asyncio.gather(*tasks)
        self.assertEqual(sum(r.status_code==200 for r in responses),1)
        self.assertEqual(len((await self.s.state(self.c))['calls'])-before,1)
        summary=(await self.usage(headers))['summary'];self.assertEqual(summary['balance_micro_usd'],0);self.assertEqual(summary['reserved_micro_usd'],0)

    async def test_06_shared_token_rpm_and_concurrency_limits(self):
        t,k=await self.s.create_user(self.c,tokens=150);k2=await self.second_key(t)
        self.assertEqual((await self.chat(headers=self.s.headers(k['api_key']))).status_code,200)
        self.assertEqual((await self.chat(headers=self.s.headers(k2['api_key']))).status_code,402)
        t,k=await self.s.create_user(self.c,rpm=1);k2=await self.second_key(t)
        self.assertEqual((await self.chat(headers=self.s.headers(k['api_key']))).status_code,200)
        r=await self.chat(headers=self.s.headers(k2['api_key']));self.assertEqual(r.status_code,429);self.assertIn('retry-after',r.headers)
        t,k=await self.s.create_user(self.c,concurrency=1);k2=await self.second_key(t)
        await self.mode('gate');first=asyncio.create_task(self.chat(headers=self.s.headers(k['api_key'])))
        async def reserved():return (await self.usage(self.s.headers(k['api_key'])))['summary']['reserved_micro_usd']==500
        await self.wait_until(reserved)
        self.assertEqual((await self.chat(headers=self.s.headers(k2['api_key']))).status_code,429)
        await self.release();self.assertEqual((await first).status_code,200)

    async def test_07_price_versions_credit_and_summary(self):
        await self.chat();p=await self.c.put(self.s.base+'/admin/pricing',headers=self.admin,json={'model':self.s.env['MODEL_ID'],'input_micro_usd_per_token':4,'output_micro_usd_per_token':8})
        self.assertEqual(p.status_code,200);await self.chat()
        report=await self.usage();self.assertEqual([x['cost_micro_usd'] for x in report['items']],[500,800])
        self.assertNotEqual(report['items'][0]['pricing_version'],report['items'][1]['pricing_version'])
        self.assertEqual(report['summary']['cost_micro_usd'],1300)
        url=self.s.base+'/admin/tenants/'+self.tenant['tenant_id']+'/credits';headers={**self.admin,'Idempotency-Key':secrets.token_hex(16)}
        for _ in range(2):self.assertEqual((await self.c.post(url,headers=headers,json={'delta_micro_usd':1000,'reason':'fixture'})).status_code,200)
        self.assertEqual((await self.c.post(url,headers=headers,json={'delta_micro_usd':2000,'reason':'fixture'})).status_code,409)
        self.assertEqual((await self.usage())['summary']['balance_micro_usd'],99700)

    async def test_08_sse_signal_gate_utf8_and_coalesced_events(self):
        await self.mode('gate')
        async with self.c.stream('POST',self.s.base+'/v1/chat/completions',headers=self.headers,json=self.s.payload(True,stream_options={'include_usage':True})) as r:
            self.assertEqual(r.status_code,200);events=r.aiter_lines()
            first=await asyncio.wait_for(anext(events),1)
            self.assertIn('第一段',first)
            # The producer is still gated. Receiving this event proves incremental forwarding.
            await self.release();tail='\n'.join([line async for line in events])
            self.assertIn('第二段',tail);self.assertIn('[DONE]',tail);self.assertIn('"usage"',tail)

    async def test_09_cancel_releases_slot_and_aborts(self):
        await self.s.configure(self.c,delay=1.)
        before=(await self.metrics())['counters']['abort_acknowledged']
        async with self.c.stream('POST',self.s.base+'/v1/chat/completions',headers=self.headers,json=self.s.payload(True)) as r:
            rid=r.headers['x-request-id']
            async for line in r.aiter_lines():
                if 'fixture ' in line:break
        async def cleaned():
            m=await self.metrics();return m['active']==0 and m['queued']==0 and m['counters']['abort_acknowledged']>before
        await self.wait_until(cleaned)
        self.assertIn(rid,(await self.s.state(self.c))['aborted'])
        row=(await self.usage())['items'][0];self.assertEqual(row['status'],'pending_reconciliation');self.assertIsNone(row['input_tokens'])

    async def test_10_missing_usage_and_missing_done_are_errors(self):
        await self.s.configure(self.c,omit_usage=True)
        r=await self.chat(stream=True);self.assertIn('"error"',r.text);self.assertNotIn('[DONE]',r.text)
        async def reconciled():
            row=(await self.usage())['items'][0];return row if row['usage_state']=='unknown_released' else None
        row=await self.wait_until(reconciled);self.assertIsNone(row['cost_micro_usd'])
        self.assertEqual((await self.usage())['summary']['reserved_micro_usd'],0)
        await self.s.configure(self.c);await self.mode('missing_done')
        r=await self.chat(stream=True);self.assertIn('"error"',r.text);self.assertNotIn('[DONE]',r.text)
        row=(await self.usage())['items'][-1];self.assertEqual(row['cost_micro_usd'],500);self.assertEqual(row['status'],'failed')

    async def test_11_queue_bounds_timeout_and_recovery(self):
        await self.mode('gate')
        tasks=[asyncio.create_task(self.chat()) for _ in range(5)]
        async def full():
            m=await self.metrics();return m['active']==2 and m['queued']==2
        await self.wait_until(full)
        await asyncio.sleep(.1)
        self.assertEqual(sum(t.done() and t.result().status_code==429 for t in tasks),1)
        await self.release();responses=await asyncio.gather(*tasks)
        self.assertEqual(sorted(r.status_code for r in responses),[200,200,200,200,429])
        m=await self.metrics();self.assertEqual((m['active'],m['queued']),(0,0))
        await self.mode('');await self.s.configure(self.c,delay=3.)
        r=await self.chat();self.assertEqual(r.status_code,504)
        m=await self.metrics();self.assertEqual((m['active'],m['queued']),(0,0))
        await self.s.configure(self.c);self.assertEqual((await self.chat()).status_code,200)

    async def test_12_restart_unknown_usage_releases_with_audit(self):
        await self.s.configure(self.c,delay=1.5)
        async with self.c.stream('POST',self.s.base+'/v1/chat/completions',headers=self.headers,json=self.s.payload(True)) as r:
            rid=r.headers['x-request-id']
            async for line in r.aiter_lines():
                if 'fixture ' in line:break
            pid=int((self.s.data/'gateway.pid').read_text());psutil.Process(pid).kill()
        self.s.start_gateway()
        row=(await self.usage())['items'][0]
        self.assertEqual(row['usage_state'],'unknown_released');self.assertIsNone(row['input_tokens']);self.assertIsNone(row['cost_micro_usd'])
        self.assertEqual((await self.usage())['summary']['reserved_micro_usd'],0)
        audit=await self.c.get(self.s.base+'/admin/audit',headers=self.admin)
        # Audit is paginated: query rows after the first page when needed.
        rows=audit.json()['items'];cursor=audit.json()['next_cursor']
        while cursor:
            data=(await self.c.get(self.s.base+'/admin/audit?cursor='+str(cursor),headers=self.admin)).json();rows+=data['items'];cursor=data['next_cursor']
        self.assertTrue(any(x['event']=='credit_adjustment' and x['resource_id']==rid for x in rows))

    async def test_13_restart_after_usage_settles_exactly_once(self):
        await self.s.configure(self.c,pause_after_usage=1.5)
        async with self.c.stream('POST',self.s.base+'/v1/chat/completions',headers=self.headers,json=self.s.payload(True,stream_options={'include_usage':True})) as r:
            async for line in r.aiter_lines():
                if '"usage"' in line:break
            pid=int((self.s.data/'gateway.pid').read_text());psutil.Process(pid).kill()
        self.s.start_gateway()
        report=await self.usage();self.assertEqual(report['summary']['cost_micro_usd'],500);self.assertEqual(report['summary']['reserved_micro_usd'],0)
        self.assertEqual(report['items'][0]['usage_state'],'exact')
        self.s.stop_gateway();self.s.start_gateway()
        self.assertEqual((await self.usage())['summary']['cost_micro_usd'],500)

    async def test_14_agent_two_calls_parent_child_and_idempotency(self):
        await self.mode('agent')
        data={'task':'Where is order X927? Return order_id, status and eta_days.','tool_data':{'orders':{'X927':{'status':'shipped','eta_days':3}}}}
        headers={**self.headers,'Idempotency-Key':secrets.token_hex(12)}
        r=await self.c.post(self.s.base+'/agent/run',headers=headers,json=data)
        self.assertEqual(r.status_code,200);v=r.json();self.assertEqual(v['answer'],{'order_id':'X927','status':'shipped','eta_days':3})
        self.assertEqual(v['usage'],{'model_calls':2,'completion_tokens':100})
        rows=(await self.usage())['items'];self.assertEqual(len(rows),3)
        parent=next(x for x in rows if x['route']=='/agent/run');children=[x for x in rows if x['parent_request_id']==parent['request_id']]
        self.assertEqual(parent['cost_micro_usd'],0);self.assertEqual(len(children),2)
        self.assertEqual(sum(x['cost_micro_usd'] for x in children),1000)
        self.assertEqual((await self.c.post(self.s.base+'/agent/run',headers=headers,json=data)).status_code,409)

    async def test_15_idempotent_setup_and_stop_preserve_fixture(self):
        await self.chat();pid=(self.s.data/'gateway.pid').read_text()
        self.s.start_gateway(setup=True)
        self.assertEqual((self.s.data/'gateway.pid').read_text(),pid)
        self.s.stop_gateway();self.assertEqual((await self.c.get(self.s.fixture+'/health')).status_code,200)
        self.s.start_gateway();self.assertEqual((await self.usage())['summary']['cost_micro_usd'],500)

    async def test_16_health_dependency_and_input_validation(self):
        before=len((await self.s.state(self.c))['calls'])
        r=await self.c.post(self.s.base+'/v1/chat/completions',headers=self.headers,json={**self.s.payload(),'user_id':'someone-else'})
        self.assertEqual(r.status_code,422)
        r=await self.c.post(self.s.base+'/agent/run',headers=self.headers,json={'task':None,'tool_data':None})
        self.assertEqual(r.status_code,422);self.assertEqual(len((await self.s.state(self.c))['calls']),before)
        # Stop only this harness's fixture, observe readiness fail, and leave the gateway running.
        self.s.fixture_process.terminate();self.s.fixture_process.wait(timeout=5)
        self.assertEqual((await self.c.get(self.s.base+'/healthz')).status_code,503)
        # Restore the owned fixture for cleanup and any subsequent tests.
        import subprocess,sys
        with (self.s.runtime/'fixture.log').open('ab') as log:
            self.s.fixture_process=subprocess.Popen([sys.executable,str(self.s.fixture_script)],cwd=self.s.runtime.parents[1],env=self.s.env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.s.wait(self.s.fixture+'/health');self.assertEqual((await self.c.get(self.s.base+'/healthz')).status_code,200)

    async def test_17_queued_timeout_and_disconnect_have_no_engine_usage(self):
        await self.mode('gate')
        before=len((await self.s.state(self.c))['calls'])
        held=[asyncio.create_task(self.chat()) for _ in range(2)]
        async def active_two():return (await self.metrics())['active']==2
        await self.wait_until(active_two)
        r=await self.chat();self.assertEqual(r.status_code,504);self.assertEqual(r.json()['error']['code'],'queue_timeout')
        queued=asyncio.create_task(self.chat())
        async def waiting():return (await self.metrics())['queued']==1
        await self.wait_until(waiting);queued.cancel()
        try:await queued
        except asyncio.CancelledError:pass
        async def empty_queue():return (await self.metrics())['queued']==0
        await self.wait_until(empty_queue)
        await self.release();self.assertTrue(all(r.status_code==200 for r in await asyncio.gather(*held)))
        self.assertEqual(len((await self.s.state(self.c))['calls'])-before,2)
        report=await self.usage();self.assertEqual(report['summary']['cost_micro_usd'],1000)
        self.assertEqual(report['summary']['reserved_micro_usd'],0)

    async def test_18_inflight_revocation_price_snapshot_and_credit_safety(self):
        tenant,key=await self.s.create_user(self.c,balance=500)
        headers=self.s.headers(key['api_key']);await self.mode('gate')
        held=asyncio.create_task(self.chat(headers=headers))
        async def reserved():return (await self.usage(headers))['summary']['reserved_micro_usd']==500
        await self.wait_until(reserved)
        r=await self.c.post(self.s.base+'/admin/tenants/'+tenant['tenant_id']+'/credits',headers={**self.admin,'Idempotency-Key':secrets.token_hex(16)},json={'delta_micro_usd':-1,'reason':'must not spend reserved funds'})
        self.assertEqual(r.status_code,402)
        r=await self.c.put(self.s.base+'/admin/pricing',headers=self.admin,json={'model':self.s.env['MODEL_ID'],'input_micro_usd_per_token':4,'output_micro_usd_per_token':8});self.assertEqual(r.status_code,200)
        r=await self.c.post(self.s.base+'/admin/keys/'+key['key_id']+'/revoke',headers=self.admin,json={});self.assertEqual(r.status_code,200)
        self.assertEqual((await self.chat(headers=headers)).status_code,401)
        await self.release();self.assertEqual((await held).status_code,200)
        r=await self.c.get(self.s.base+'/admin/usage?tenant_id='+tenant['tenant_id'],headers=self.admin)
        self.assertEqual(r.json()['summary']['cost_micro_usd'],500);self.assertEqual(r.json()['summary']['balance_micro_usd'],0)

    async def test_19_malformed_sse_reports_error_without_done(self):
        await self.mode('invalid_json');r=await self.chat(stream=True)
        self.assertIn('invalid_sse_json',r.text);self.assertNotIn('[DONE]',r.text)
        self.assertEqual((await self.metrics())['active'],0)
        await self.mode('');self.assertEqual((await self.chat()).status_code,200)
