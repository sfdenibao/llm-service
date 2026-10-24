"""CPU-only protocol diagnostics inside an isolated submission sandbox.

The orchestrator starts credential_fixture.py and runs setup.sh --contract-test
BEFORE invoking this client. This file never executes submission code, launches
processes, restarts services, allocates resources, or grants official points.
Requires httpx. Credentials come from environment, never command arguments.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
from pathlib import Path
import secrets
from urllib.parse import quote, urlsplit

import httpx

SCHEMA = 'llm-soft-check/1'
LIMIT = 256_000
STATES = ('pass', 'missing', 'invalid', 'blocked')


class Finding(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def need(condition, code='response_schema'):
    if not condition:
        raise Finding('invalid', code)


def loopback_url(value):
    try:
        p = urlsplit(value)
        address = ipaddress.ip_address(p.hostname or '')
        if (p.scheme != 'http' or not address.is_loopback or not p.port or
                p.username is not None or p.password is not None or
                p.path not in ('', '/') or p.query or p.fragment):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Only explicit-port HTTP literal loopback origins are accepted') from None
    return value.rstrip('/')


def usage_ok(value):
    return (isinstance(value, dict) and
            all(type(value.get(k)) is int for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) and
            value['prompt_tokens'] == 100 and value['completion_tokens'] == 50 and value['total_tokens'] == 150)


def object_id(value, name):
    need(isinstance(value, dict) and isinstance(value.get(name), str) and 0 < len(value[name]) <= 200)
    return quote(value[name], safe='')


async def run_checks(base, fixture, key, admin_key, fixture_key, model, revision, *, transport=None, timeout=8, total_timeout=90, commit_sha=None, repository=None):
    """Return independently classified interfaces; transport injection is CPU-test only.

    Missing = HTTP404/405/501; invalid = reachable but violated contract;
    blocked = prerequisite/configuration/transport unavailable. A failure does
    not turn later independent interfaces into unexecuted successes.
    """
    if commit_sha is not None and not re.fullmatch(r"[a-f0-9]{40}", commit_sha):
        raise ValueError("Invalid source commit")
    if repository is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    base, fixture = loopback_url(base), loopback_url(fixture)
    if base == fixture:
        raise ValueError('Gateway and controlled fixture must use different origins')
    if not all(isinstance(x, str) and x for x in (key, admin_key, fixture_key, model, revision)):
        raise ValueError('All environment credentials and model identity are required')
    if not 0 < timeout <= 30 or not 0 < total_timeout <= 180:
        raise ValueError('Invalid bounded timeout')
    rows, values, created = [], {}, []
    started = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False, transport=transport) as client:
        async def raw(method, path, *, credential=key, data=None, status=200, origin=base, extra=None):
            headers = {'Authorization': 'Bearer ' + credential} if credential else {}
            headers.update(extra or {})
            async with client.stream(method, origin + path, headers=headers, json=data) as response:
                if response.status_code in (404, 405, 501):
                    raise Finding('missing', 'route_unavailable')
                need(response.status_code == status, 'unexpected_http_status')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    need(len(body) <= LIMIT, 'response_too_large')
                return bytes(body), response.headers

        async def req(method, path, **kw):
            body, _ = await raw(method, path, **kw)
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                raise Finding('invalid', 'invalid_json') from None
            need(isinstance(value, dict), 'expected_json_object')
            return value

        async def check(ident, method, path, expected, fn, deps=()):
            row = {'id': ident, 'method': method, 'path': path, 'expected': expected}
            if any(dep not in values for dep in deps):
                row.update(status='blocked', code='prerequisite_failed', evidence={})
            else:
                try:
                    value, evidence = await asyncio.wait_for(fn(), timeout=timeout * 3)
                    values[ident] = value
                    row.update(status='pass', code='contract_satisfied', evidence=evidence)
                except Finding as exc:
                    row.update(status=exc.status, code=exc.code, evidence={})
                except (httpx.HTTPError, asyncio.TimeoutError):
                    row.update(status='blocked', code='transport_unavailable', evidence={})
                except (ValueError, KeyError, TypeError, IndexError, AttributeError):
                    row.update(status='invalid', code='response_schema', evidence={})
            rows.append(row)

        async def fixture_ready():
            state = await req('GET', '/__fixture/state', origin=fixture, credential=fixture_key)
            need(isinstance(state.get('calls'), list), 'fixture_schema')
            await req('POST', '/__fixture/configure', origin=fixture, credential=fixture_key,
                      data={'prompt_tokens': 100, 'completion_tokens': 50, 'delay': 0.,
                            'omit_usage': False, 'fail_before': False, 'pause_after_usage': 0.})
            models = await req('GET', '/v1/models', origin=fixture, credential='')
            need(any(x.get('id') == model for x in models.get('data', [])), 'fixture_model_mismatch')
            return True, {'backend': 'controlled_cpu_fixture'}

        async def health():
            v = await req('GET', '/healthz', credential='')
            need(v.get('status') == 'ready', 'not_ready')
            return v, {'ready': True}

        async def auth():
            for path in ('/v1/models', '/info', '/v1/usage'):
                async with client.stream('GET', base + path) as r:
                    if r.status_code in (404, 405, 501):
                        raise Finding('missing', 'route_unavailable')
                    need(r.status_code in (401, 403), 'unauthenticated_access')
            return True, {'routes_denied': 3}

        async def models():
            v = await req('GET', '/v1/models')
            need(isinstance(v.get('data'), list) and any(x.get('id') == model for x in v['data']), 'model_mismatch')
            return v, {'model_matches': True}

        async def info():
            v = await req('GET', '/info')
            need(v.get('contract') == 'llm-lab/1.0' and v.get('model_id') == model and
                 v.get('model_revision') == revision and v.get('profile') in ('baseline', 'optimized') and
                 isinstance(v.get('config'), dict), 'info_identity_or_schema')
            return v, {'identity_matches': True, 'gpu_deployment_verified': False}

        async def metrics():
            v=await req('GET','/metrics')
            need(all(type(v.get(field)) is int and v[field]>=0 for field in ('active','queued')),
                 'metrics_gauges')
            counters=v.get('counters')
            need(isinstance(counters,dict) and type(counters.get('abort_acknowledged')) is int and
                 counters['abort_acknowledged']>=0,'metrics_abort_counter')
            async with client.stream('GET',base+'/metrics') as response:
                need(response.status_code in (401,403),'unauthenticated_metrics')
            return True, {'schema_valid':True,'unauthenticated_denied':True,'cancellation_verified':False}

        async def agent_request_validation():
            # No valid task is submitted: the CPU fixture cannot reason or execute tools.
            before=await fixture_calls()
            bad={'task':None,'tool_data':None}
            async with client.stream('POST',base+'/agent/run',json=bad) as response:
                unauthorized=response.status_code
            async with client.stream('POST',base+'/agent/run',json=bad,
                    headers={'Authorization':'Bearer '+key}) as response:
                malformed=response.status_code
            after=await fixture_calls()
            if malformed in (404,405,501):
                raise Finding('missing','agent_route_unavailable')
            need(unauthorized in (401,403),'unauthenticated_agent')
            need(malformed in (400,422),'agent_invalid_payload_accepted')
            need(before==after,'agent_invalid_payload_called_fixture')
            return True, {'unauthenticated_denied':True,'malformed_payload_rejected':True,
                          'fixture_calls':0,'agent_reasoning_or_output_tested':False}

        async def fixture_calls():
            v = await req('GET', '/__fixture/state', origin=fixture, credential=fixture_key)
            need(isinstance(v.get('calls'), list), 'fixture_schema')
            return len(v['calls'])

        payload = {'model': model, 'messages': [{'role': 'user', 'content': 'CPU protocol fixture'}],
                   'temperature': 0, 'max_tokens': 50, 'stream': False}

        async def infer(stream=False, credential=key):
            before = await fixture_calls()
            if not stream:
                v = await req('POST', '/v1/chat/completions', credential=credential, data=payload)
                choice = v['choices'][0]
                need(choice['message'].get('content') == 'fixture result' and choice.get('finish_reason') is not None,
                     'fixture_output_or_finish')
                need(usage_ok(v.get('usage')), 'fixture_usage')
            else:
                data, headers = await raw('POST', '/v1/chat/completions', credential=credential,
                    data={**payload, 'stream': True, 'stream_options': {'include_usage': True}})
                need('text/event-stream' in headers.get('content-type', ''), 'not_sse')
                text, done, finished, usage, seen_done = '', False, False, None, False
                # Same SSE data-line framing as the public measurement parser.
                event_data = []
                for line in data.decode().splitlines() + ['']:
                    if line.startswith('data:'):
                        event_data.append(line[5:].removeprefix(' '))
                    elif not line and event_data:
                        event = '\n'.join(event_data); event_data = []
                        need(not seen_done, 'events_after_done')
                        if event == '[DONE]':
                            done = seen_done = True
                            continue
                        value = json.loads(event)
                        need(isinstance(value, dict) and 'error' not in value, 'sse_error')
                        if value.get('usage') is not None: usage = value['usage']
                        for choice in value.get('choices', []):
                            part = choice.get('delta', {}).get('content') or ''
                            need(isinstance(part, str), 'sse_content_type')
                            text += part
                            finished = finished or choice.get('finish_reason') is not None
                need(done and finished and text == 'fixture result', 'incomplete_sse')
                need(usage_ok(usage), 'fixture_usage')
            need(await fixture_calls() == before + 1, 'fixture_not_used_exactly_once')
            return True, {'fixture_calls': 1, 'prompt_tokens': 100, 'completion_tokens': 50,
                          'stream': stream, 'latency_measured': False}

        async def console():
            data, headers = await raw('GET', '/console', credential='')
            need('text/html' in headers.get('content-type', '') and bool(data.strip()), 'console_not_html')
            need(all(s.encode() not in data for s in (key, admin_key, fixture_key)), 'credential_in_console')
            return True, {'html_available': True, 'interactive_ui_tested': False}

        async def pricing():
            v = await req('PUT', '/admin/pricing', credential=admin_key,
                data={'model': model, 'input_micro_usd_per_token': 2, 'output_micro_usd_per_token': 6})
            object_id(v, 'pricing_version')
            need(v.get('model') == model and type(v.get('input_micro_usd_per_token')) is int and
                 v['input_micro_usd_per_token'] == 2 and type(v.get('output_micro_usd_per_token')) is int and
                 v['output_micro_usd_per_token'] == 6, 'pricing_model_or_rates')
            return v, {'input_price': 2, 'output_price': 6}

        async def tenant():
            v = await req('POST', '/admin/tenants', credential=admin_key, status=201,
                data={'name': 'soft-check-' + secrets.token_hex(6), 'balance_micro_usd': 10000,
                      'token_limit': 10000, 'rpm_limit': 50, 'concurrency_limit': 4})
            ident = object_id(v, 'tenant_id'); created.append(ident)
            need(v.get('balance_micro_usd') == 10000, 'tenant_balance')
            return (ident, v), {'created': True}

        async def issue():
            tid = values['tenant'][0]
            v = await req('POST', f'/admin/tenants/{tid}/keys', credential=admin_key, status=201, data={'name': 'soft-check'})
            object_id(v, 'key_id'); need(isinstance(v.get('api_key'), str) and len(v['api_key']) >= 32, 'issued_key_schema')
            return v, {'created': True, 'raw_key_retained_in_report': False}

        async def bill():
            v = values['issue']; await infer(credential=v['api_key']); await infer(True, v['api_key'])
            ledger = await req('GET', '/v1/usage', credential=v['api_key']); items = ledger['items']; s = ledger['summary']
            need(len(items) == 2 and all(x['tenant_id'] == values['tenant'][1]['tenant_id'] and
                x['key_id'] == v['key_id'] and x['input_tokens'] == 100 and x['output_tokens'] == 50 and
                x['cost_micro_usd'] == 500 and x.get('status')=='completed' and x.get('model')==model and
                x.get('pricing_version')==values['pricing']['pricing_version'] and
                all(type(x.get(field)) is int for field in ('input_tokens','output_tokens','cost_micro_usd')) for x in items), 'ledger_attribution_or_usage')
            need(all(type(s.get(field)) is int for field in ('cost_micro_usd','balance_micro_usd','reserved_micro_usd')) and
                 s['cost_micro_usd'] == 1000 and s['balance_micro_usd'] == 9000 and s['reserved_micro_usd'] == 0,
                 'ledger_totals')
            return ledger, {'requests': 2, 'total_micro_usd': 1000}

        async def management_isolation():
            # Deliberately self-contained: no private grader modules are shipped.
            v=values['issue'];tid=values['tenant'][0];kid=object_id(v,'key_id')
            async def snapshot():
                return [await req('GET',path,credential=admin_key) for path in
                    ('/admin/tenants',f'/admin/tenants/{tid}/keys','/admin/pricing',f'/admin/usage?tenant_id={tid}')]
            before=await snapshot()
            writes=[
                ('POST','/admin/tenants',{'name':'denied-soft-probe','balance_micro_usd':123,'token_limit':1000,'rpm_limit':5,'concurrency_limit':1}),
                ('PATCH',f'/admin/tenants/{tid}',{'status':'disabled'}),
                ('POST',f'/admin/tenants/{tid}/credits',{'delta_micro_usd':17,'reason':'denied-soft-probe'}),
                ('POST',f'/admin/tenants/{tid}/keys',{'name':'denied-soft-probe'}),
                ('POST',f'/admin/keys/{kid}/rotate',{}),
                ('POST',f'/admin/keys/{kid}/revoke',{}),
                ('PUT','/admin/pricing',{'model':model,'input_micro_usd_per_token':3,'output_micro_usd_per_token':7}),
            ]
            statuses=[]
            for method,path,data in writes:
                async with client.stream(method,base+path,json=data,headers={'Authorization':'Bearer '+key,
                        'Idempotency-Key':'soft-denied-'+secrets.token_hex(12)}) as response:
                    statuses.append(response.status_code)
            after=await snapshot()
            need(before==after,'unauthorized_management_mutation')
            if any(status in (404,405,501) for status in statuses):
                raise Finding('missing','management_route_unavailable')
            need(all(status in (401,403) for status in statuses),'management_write_not_denied')
            return True, {'denied_write_routes':len(writes),'management_state_unchanged':True}

        async def rotate():
            old = values['issue'];kid=object_id(old, 'key_id')
            v=await req('POST', f'/admin/keys/{kid}/rotate', credential=admin_key, data={})
            need(isinstance(v.get('api_key'), str) and len(v['api_key'])>=32 and v['api_key']!=old['api_key'], 'rotation_key')
            need(v.get('key_id')!=old['key_id'], 'rotation_id');object_id(v,'key_id')
            await req('GET','/v1/usage',credential=old['api_key'],status=401)
            await infer(credential=v['api_key'])
            return v, {'old_key_http':401,'new_key_works':True}

        async def revoke():
            v=values['rotate'];kid=object_id(v,'key_id')
            await req('POST',f'/admin/keys/{kid}/revoke',credential=admin_key,data={})
            await req('GET','/v1/usage',credential=v['api_key'],status=401)
            return True, {'revoked_key_http':401}

        async def disable():
            tid=values['tenant'][0]
            fresh=await req('POST',f'/admin/tenants/{tid}/keys',credential=admin_key,status=201,data={'name':'disable-probe'})
            need(isinstance(fresh.get('api_key'),str) and len(fresh['api_key'])>=32,'issued_key_schema')
            await req('PATCH',f'/admin/tenants/{tid}',credential=admin_key,data={'status':'disabled'})
            await req('GET','/v1/usage',credential=fresh['api_key'],status=403)
            listing=await req('GET','/admin/tenants',credential=admin_key)
            need(any(x.get('tenant_id')==values['tenant'][1]['tenant_id'] and x.get('status')=='disabled'
                     for x in listing.get('items',[])), 'disabled_status_not_persisted')
            return True, {'disabled_key_http':403,'disabled_status_persisted':True}

        plan=[
            ('fixture','GET','/__fixture/state','Controlled CPU fixture available and configured',fixture_ready,()),
            ('health','GET','/healthz','Public ready health response',health,()),
            ('auth','GET','/v1/models, /info, /v1/usage','Unauthenticated requests denied',auth,()),
            ('models','GET','/v1/models','Configured model advertised',models,()),
            ('info','GET','/info','Contract/model/revision/profile identity',info,()),
            ('metrics','GET','/metrics','Authenticated nonnegative integer gauges and abort counter',metrics,()),
            ('agent_request_validation','POST','/agent/run','Route/auth/malformed-payload validation only; no Agent reasoning or output test',agent_request_validation,('fixture',)),
            ('nonstream','POST','/v1/chat/completions','Fixture content, finish and 100/50 usage',infer,('fixture',)),
            ('stream','POST','/v1/chat/completions','SSE content, finish, usage and DONE',lambda:infer(True),('fixture',)),
            ('console','GET','/console','Non-secret HTML shell (UI behavior not checked)',console,()),
            ('pricing','PUT','/admin/pricing','Model price version with integer 2/6 rates',pricing,('fixture',)),
            ('tenant','POST','/admin/tenants','Create isolated synthetic tenant',tenant,('fixture',)),
            ('issue','POST','/admin/tenants/{tenant_id}/keys','Issue one-time ordinary key',issue,('tenant',)),
            ('billing','GET','/v1/usage','Two real gateway transactions total 1000 microUSD',bill,('fixture','pricing','issue')),
            ('admin_isolation','MULTI','/admin/*','Ordinary key denied all seven management writes without mutation',management_isolation,('issue',)),
            ('rotate','POST','/admin/keys/{key_id}/rotate','Revoke old key and accept replacement',rotate,('fixture','issue')),
            ('revoke','POST','/admin/keys/{key_id}/revoke','Revoke replacement key',revoke,('rotate',)),
            ('disable','PATCH','/admin/tenants/{tenant_id}','Disable synthetic tenant',disable,('tenant',)),
        ]
        async def execute_plan():
            for args in plan: await check(*args)
        try:
            await asyncio.wait_for(execute_plan(),timeout=total_timeout)
        except asyncio.TimeoutError:
            existing={row['id'] for row in rows}
            for ident,method,path,expected,_,_ in plan:
                if ident not in existing:
                    rows.append({'id':ident,'method':method,'path':path,'expected':expected,
                                 'status':'blocked','code':'total_deadline','evidence':{}})
        finally:
            for tid in created:
                try:
                    await asyncio.wait_for(req('PATCH',f'/admin/tenants/{tid}',credential=admin_key,
                        data={'status':'disabled'}),timeout=2)
                except Exception: pass  # Sandbox lifecycle remains the orchestrator's responsibility.
    summary={state:sum(row['status']==state for row in rows) for state in STATES}
    return {'schema':SCHEMA,'project':'project-2','mode':'soft','commit_sha':commit_sha,'repository':repository,'suite':'cpu-contract','official_score':False,'gpu_used':False,
            'model_backend':'controlled_cpu_fixture','started_at':started,
            'finished_at':datetime.now(timezone.utc).isoformat(),'checks':rows,'summary':summary,
            'passed':all(row['status']=='pass' for row in rows),
            'not_tested':['GPU deployment','model quality','performance or SLO','Agent model reasoning or output',
                          'full 15-case credential audit','crash recovery','persistent raw-key storage','interactive console UI'],
            'limitations':['Runs after setup --contract-test inside an isolated CPU sandbox; never executes setup itself.',
                          'Fixture observation checks gateway integration, not GPU attestation or official grading.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--fixture-url',default='http://127.0.0.1:30001')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    try:
        result=asyncio.run(run_checks(args.base_url,args.fixture_url,
            os.environ.get('API_KEY',''),os.environ.get('ADMIN_API_KEY',''),os.environ.get('FIXTURE_CONTROL_KEY',''),
            os.environ.get('MODEL_ID',''),os.environ.get('MODEL_REVISION',''),
            commit_sha=os.environ.get('SOURCE_COMMIT',''),repository=os.environ.get('GITHUB_REPOSITORY','')))
        destination=Path(args.out)
        # Exclusive creation avoids clobbering another diagnostic or following a symlink.
        with destination.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    except (ValueError,OSError):
        raise SystemExit('Invalid configuration or unavailable output path') from None
    print(json.dumps({'schema':SCHEMA,'official_score':False,'summary':result['summary'],'passed':result['passed']}))
    raise SystemExit(0 if result['passed'] else 1)

if __name__=='__main__':main()
