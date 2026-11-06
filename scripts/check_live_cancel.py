"""HTTP-only live cancellation probe; API_KEY stays in memory. No GPU attestation.

Run on the service host (or an explicit loopback tunnel) while it is idle:
  .venv-bench/bin/python scripts/check_live_cancel.py \
    --base-url http://127.0.0.1:8000 --out results/live-cancel-01.json
This creates two ordinary, billed requests. Unknown interrupted usage remains
unknown; abort acknowledgement does not prove GPU work or memory was released.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


def now():
    return datetime.now(timezone.utc).isoformat()


def origin(value):
    value = value.rstrip('/')
    parts = urlsplit(value)
    try:
        valid = ipaddress.ip_address(parts.hostname).is_loopback and parts.port is not None
    except (ValueError, TypeError):
        valid = False
    if not valid or parts.scheme != 'http' or any((parts.path, parts.query, parts.fragment,
                                                 parts.username, parts.password)):
        raise ValueError('Use an explicit loopback HTTP origin with port')
    return value


def selected(row):
    keys = ('request_id', 'status', 'usage_state', 'input_tokens', 'output_tokens',
            'cost_micro_usd', 'reserved_micro_usd', 'reserved_tokens', 'pricing_version', 'error_code')
    return {key: row.get(key) for key in keys} if row else None


def gauges(value):
    return {key: value.get(key) for key in ('active', 'queued')}


def terminal(row):
    return bool(row and row.get('status') in ('cancelled', 'interrupted')
                and row.get('usage_state') in ('exact', 'unknown_released')
                and row.get('reserved_micro_usd') == 0 and row.get('reserved_tokens') == 0)


async def run(base, key, report):
    report['phase'] = 'preconditions'
    def check(name, expected, actual, passed):
        report['checks'].append({'test_id': name, 'expected': expected, 'actual': actual,
                                 'passed': bool(passed)})
        return bool(passed)

    async with httpx.AsyncClient(base_url=base, headers={'Authorization': 'Bearer ' + key},
                                timeout=5, trust_env=False, follow_redirects=False) as client:
        async def get(path, params=None):
            response = await client.get(path, params=params)
            if response.status_code != 200:
                raise RuntimeError('Unexpected HTTP status ' + str(response.status_code))
            return response.json()

        async def usage(cursor=0):
            rows = []
            for _ in range(100):
                value = await get('/v1/usage', {'cursor': cursor, 'limit': 200})
                rows.extend(value['items'])
                following = value.get('next_cursor')
                if following is None:
                    return value['summary'], rows
                if type(following) is not int or following <= cursor:
                    raise ValueError('Invalid usage cursor')
                cursor = following
            raise ValueError('Usage pagination exceeded probe limit')

        async with asyncio.timeout(20):
            info = await get('/info')
            report['service'] = {k: info.get(k) for k in ('model_id', 'model_revision', 'profile', 'engine', 'mode')}
            if not check('live_service', 'Declared live SGLang service', report['service'],
                         info.get('mode') == 'live' and info.get('engine') == 'sglang'):
                return
            before_metrics = await get('/metrics')
            before_summary, before_rows = await usage()
        idle = gauges(before_metrics) == {'active': 0, 'queued': 0}
        idle = idle and before_summary.get('reserved_micro_usd') == 0 and before_summary.get('reserved_tokens') == 0
        if not check('idle_precondition', 'No concurrent workload or unresolved reservations',
                     {'gauges': gauges(before_metrics), 'reserved_micro_usd': before_summary.get('reserved_micro_usd'),
                      'reserved_tokens': before_summary.get('reserved_tokens')}, idle):
            return
        cursor = max((r['sequence'] for r in before_rows), default=0)
        report['phase'] = 'stream_until_first_content'
        report['stream'] = {'request_id': None, 'first_content_received': False, 'disconnect_before_finish': False}
        long_request = {'model': info['model_id'], 'messages': [{'role': 'user', 'content':
                        'Write the integers from 1 through 500 in order, one per line. Continue until 500; do not summarize.'}],
                        'temperature': 0, 'max_tokens': 2048, 'stream': True,
                        'stream_options': {'include_usage': True}}
        started = time.monotonic()
        finish_seen = False
        async with asyncio.timeout(45):
            async with client.stream('POST', '/v1/chat/completions', json=long_request, timeout=45) as response:
                if response.status_code != 200 or 'text/event-stream' not in response.headers.get('content-type', ''):
                    raise RuntimeError('Long stream did not return HTTP 200 SSE')
                rid = response.headers.get('x-request-id')
                report['stream']['request_id'] = rid
                if not rid:
                    raise ValueError('Long stream omitted X-Request-ID')
                data = []
                async for line in response.aiter_lines():
                    if line:
                        if line.startswith('data:'):
                            data.append(line[5:].lstrip())
                        if sum(map(len, data)) > 256000:
                            raise ValueError('SSE event exceeds probe limit')
                        continue
                    if not data:
                        continue
                    raw = '\n'.join(data)
                    data = []
                    if raw == '[DONE]':
                        finish_seen = True
                        break
                    event = json.loads(raw)
                    if 'error' in event:
                        raise RuntimeError('Long stream reported an error')
                    choices = event.get('choices', [])
                    finish_seen = finish_seen or any(c.get('finish_reason') is not None for c in choices)
                    if any(isinstance(c.get('delta', {}).get('content'), str) and c['delta']['content'] for c in choices):
                        report['stream'].update(first_content_received=True,
                            ttft_ms=round((time.monotonic() - started) * 1000, 3),
                            disconnect_before_finish=not finish_seen)
                        # Exiting closes the unread HTTP response instead of consuming the generation.
                        break
        report['stream']['connection_closed_at'] = now()
        check('client_disconnect', 'Close the stream immediately after nonempty content, before finish/DONE',
              report['stream'], report['stream']['first_content_received'] and not finish_seen)

        poll_start = time.monotonic()
        report['phase'] = 'cancel_convergence'
        row = None
        after_metrics = before_metrics
        report['poll_samples'] = []
        try:
            async with asyncio.timeout(60):
                while True:
                    after_metrics = await get('/metrics')
                    summary, rows = await usage(cursor)
                    row = next((r for r in rows if r['request_id'] == rid), None)
                    report['poll_samples'].append({'elapsed_seconds': round(time.monotonic() - poll_start, 3),
                        'gauges': gauges(after_metrics), 'ledger': selected(row),
                        'reserved_micro_usd': summary.get('reserved_micro_usd'),
                        'reserved_tokens': summary.get('reserved_tokens')})
                    if terminal(row) and gauges(after_metrics) == {'active': 0, 'queued': 0}:
                        break
                    await asyncio.sleep(.5)
        except TimeoutError:
            report['poll_timeout'] = True
        check('cancel_convergence', 'Within 60 seconds: cancelled/interrupted terminal bill, released reservations, active=queued=0',
              {'ledger': selected(row), 'gauges': gauges(after_metrics),
               'elapsed_seconds': round(time.monotonic() - poll_start, 3)},
              terminal(row) and gauges(after_metrics) == {'active': 0, 'queued': 0})
        if row and row.get('usage_state') == 'unknown_released':
            billing_ok = all(row.get(k) is None for k in ('input_tokens', 'output_tokens', 'cost_micro_usd'))
            boundary = 'Actual interrupted token counts/cost are unknown; released reservation is not a zero-cost measurement. Audit entry not checked by this ordinary key.'
        else:
            billing_ok = bool(row and row.get('usage_state') == 'exact' and
                             all(type(row.get(k)) is int and row[k] >= 0 for k in ('input_tokens', 'output_tokens', 'cost_micro_usd')))
            boundary = 'Exact means the service received trusted upstream usage; HTTP evidence alone does not independently measure GPU work.'
        check('interrupted_billing', 'Unknown usage stays null, or exact usage contains nonnegative integers',
              {'ledger': selected(row), 'boundary': boundary}, billing_ok)
        def delta(name):
            return after_metrics.get('counters', {}).get(name, 0) - before_metrics.get('counters', {}).get(name, 0)
        report['abort_observation'] = {'acknowledged_delta': delta('abort_acknowledged'),
            'failed_delta': delta('abort_failed'), 'gpu_task_release_proven': False,
            'meaning': 'Abort ACK only confirms the engine HTTP endpoint acknowledged a request; no engine scheduler or GPU trace was inspected.'}

        report['phase'] = 'recovery_request'
        async with asyncio.timeout(45):
            recovery_before, _ = await usage(cursor)
            response = await client.post('/v1/chat/completions', json={
                'model': info['model_id'], 'messages': [{'role': 'user', 'content': 'Reply exactly OK.'}],
                'temperature': 0, 'max_tokens': 16, 'stream': False}, timeout=40)
            if response.status_code != 200:
                raise RuntimeError('Recovery request HTTP status ' + str(response.status_code))
            recovery_id = response.headers.get('x-request-id')
            value = response.json()
            recovered_summary, recovered_rows = await usage(cursor)
            recovery_metrics = await get('/metrics')
        matches = [r for r in recovered_rows if r['request_id'] == recovery_id]
        bill = matches[0] if len(matches) == 1 else None
        returned_usage = value.get('usage', {})
        text = value.get('choices', [{}])[0].get('message', {}).get('content')
        completed = (bill and bill.get('status') == 'completed' and bill.get('usage_state') == 'exact'
                     and isinstance(text, str) and text.strip() == 'OK')
        check('recovery_request', 'A subsequent small request succeeds with OK and one completed bill',
              {'request_id': recovery_id, 'http_status': response.status_code,
               'answer_matches': text == 'OK' if isinstance(text, str) else False, 'ledger': selected(bill)}, completed)
        consistent = bool(completed and bill['input_tokens'] == returned_usage.get('prompt_tokens')
                          and bill['output_tokens'] == returned_usage.get('completion_tokens')
                          and type(bill['cost_micro_usd']) is int and bill['cost_micro_usd'] >= 0
                          and recovered_summary['cost_micro_usd'] - recovery_before['cost_micro_usd'] == bill['cost_micro_usd']
                          and recovery_before['balance_micro_usd'] - recovered_summary['balance_micro_usd'] == bill['cost_micro_usd']
                          and recovered_summary['request_count'] - recovery_before['request_count'] == 1
                          and recovered_summary['used_tokens'] - recovery_before['used_tokens'] == bill['input_tokens'] + bill['output_tokens']
                          and recovered_summary['reserved_micro_usd'] == recovered_summary['reserved_tokens'] == 0
                          and gauges(recovery_metrics) == {'active': 0, 'queued': 0})
        check('recovery_billing', 'Returned token usage, single ledger row, balance/cost/token deltas agree; idle with no reservations',
              {'returned_usage': {k: returned_usage.get(k) for k in ('prompt_tokens', 'completion_tokens')},
               'ledger': selected(bill), 'summary_before': recovery_before, 'summary_after': recovered_summary,
               'gauges': gauges(recovery_metrics)}, consistent)
        report['phase'] = 'finished'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Choose a new output file; existing evidence is preserved')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'llm-live-cancel-probe/1', 'started_at': now(), 'complete': False,
              'official_score': False, 'gpu_task_release_proven': False, 'checks': [],
              'limitations': ['Run while this service and tenant are idle.',
                  'HTTP cancellation, ledger convergence and subsequent inference are observed.',
                  'No engine scheduler inspection or GPU kernel/memory-release proof.',
                  'Pricing formula and audit entries are not inspected with an ordinary API key.']}
    # Reserve the output before sending requests. Failure and timeout paths keep their report.
    with args.out.open('x', encoding='utf-8') as output:
        try:
            base = origin(args.base_url)
            key = os.environ.get('API_KEY')
            if not key:
                raise ValueError('API_KEY is required in the environment')
            report['base_url'] = base
            asyncio.run(run(base, key, report))
            report['complete'] = True
        except (Exception, KeyboardInterrupt) as error:
            report['failure'] = {'type': type(error).__name__,
                                 'message': 'Probe interrupted or failed; inspect the completed checks. Raw exception text omitted.'}
        finally:
            report['finished_at'] = now()
            report['passed'] = report['complete'] and len(report['checks']) == 7 and all(c['passed'] for c in report['checks'])
            encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
            for name in ('API_KEY', 'ADMIN_API_KEY', 'KEY_HASH_SECRET'):
                secret = os.environ.get(name)
                if secret:
                    encoded = encoded.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
                    encoded = encoded.replace(secret, '[REDACTED]')
            output.write(encoded + '\n')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'complete': report['complete'], 'gpu_task_release_proven': False, 'official_score': False}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
