"""Closed-loop streaming benchmark. Python 3.10+, httpx 0.28.1.

One client worker starts its next request only after the previous finishes.
SSE events are NOT tokens. Quality and latency goodput are reported separately.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx


def percentile(values, q):
    values = sorted(x for x in values if x is not None)
    return values[max(0, math.ceil(len(values) * q) - 1)] if values else None


def quality_check(text, rule):
    if rule is None:
        return None
    if set(rule) != {'json_fields'} or not isinstance(rule['json_fields'], dict) or not rule['json_fields']:
        raise ValueError('quality rule must contain json_fields only')
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return False
    return isinstance(value, dict) and all(value.get(k) == v for k, v in rule['json_fields'].items())


async def events(response):
    data = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield '\n'.join(data)
                data = []
        elif line.startswith('data:'):
            data.append(line[5:].removeprefix(' '))
    if data:
        yield '\n'.join(data)


async def request_once(client, url, model, item, deadline_s, key, ident, capture=False):
    start = time.perf_counter()
    row = {'id': ident, 'workload_id': item['id'], 'kind': item.get('kind', 'unknown'),
           'ok': False, 'status': None, 'error': None, 'ttft_proxy_ms': None,
           'tpot_proxy_ms': None, 'output_tokens': None, 'quality_pass': None,
           'content_events': 0, 'input_tokens': item.get('input_tokens')}
    first = last = None
    text = ''

    async def consume():
        nonlocal first, last, text
        payload = {'model': model, 'messages': item['messages'], 'temperature': 0,
                   'max_tokens': item['max_tokens'], 'stream': True,
                   'stream_options': {'include_usage': True}}
        headers = {'X-Request-ID': ident}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        finished = done = False
        async with client.stream('POST', url, json=payload, headers=headers) as response:
            row['status'] = response.status_code
            if response.status_code != 200:
                row['error'] = 'http_' + str(response.status_code)
                return
            if 'text/event-stream' not in response.headers.get('content-type', ''):
                raise ValueError('not_sse')
            async for raw in events(response):
                now = time.perf_counter()
                if raw.strip() == '[DONE]':
                    done = True
                    break
                obj = json.loads(raw)
                if not isinstance(obj, dict) or obj.get('error') is not None:
                    raise ValueError('stream_error')
                count = (obj.get('usage') or {}).get('completion_tokens')
                if count is not None:
                    if type(count) is not int or count < 0:
                        raise ValueError('invalid_usage')
                    row['output_tokens'] = count
                for choice in obj.get('choices', []):
                    if choice.get('index', 0) != 0:
                        raise ValueError('multiple_choices_not_supported')
                    if choice.get('finish_reason') is not None:
                        finished = True
                    part = (choice.get('delta') or {}).get('content')
                    if part:
                        if not isinstance(part, str):
                            raise ValueError('non_text_delta')
                        first = now if first is None else first
                        last = now
                        row['content_events'] += 1
                        text += part
                        if len(text) > 2_000_000:
                            raise ValueError('response_too_large')
            if not done or not finished or first is None:
                raise ValueError('incomplete_stream')
            row['ok'] = True

    try:
        # Covers HTTP pool wait, connect, headers and the ENTIRE stream, even trickle traffic.
        await asyncio.wait_for(consume(), timeout=deadline_s)
    except asyncio.TimeoutError:
        row['error'] = 'deadline'
    except (ValueError, TypeError, KeyError, AttributeError):
        row['error'] = 'protocol_error'
    except httpx.HTTPError as exc:
        row['error'] = type(exc).__name__
    row['e2e_ms'] = (time.perf_counter() - start) * 1000
    row['ttft_proxy_ms'] = (first - start) * 1000 if first is not None else None
    n = row['output_tokens']
    if row['ok'] and n is not None and n > 1:
        row['tpot_proxy_ms'] = (last - first) * 1000 / (n - 1)
    if row['ok']:
        row['quality_pass'] = quality_check(text, item.get('quality'))
    row['output_sha256'] = hashlib.sha256(text.encode()).hexdigest()
    if capture:
        row['text'] = text
    return row


def summarize(rows, seconds, ttft_slo_ms, e2e_slo_ms):
    success = [r for r in rows if r['ok']]
    counts = [r['output_tokens'] for r in success]
    known_tokens = sum(n for n in counts if n is not None)
    quality_complete = bool(success) and all(r['quality_pass'] is not None for r in success)
    good_latency = [r for r in success if r['ttft_proxy_ms'] <= ttft_slo_ms
                    and r['e2e_ms'] <= e2e_slo_ms]
    token_complete = bool(success) and all(n is not None for n in counts)
    result = {'attempted': len(rows), 'success': len(success), 'failed': len(rows)-len(success),
              'window_s': seconds, 'success_rate': len(success)/len(rows) if rows else None,
              'errors': dict(Counter(r['error'] for r in rows if not r['ok'])),
              'request_rps': len(success)/seconds,
              'output_tokens_per_s': known_tokens/seconds if token_complete else None,
              'missing_usage': sum(n is None for n in counts),
              'latency_goodput_rps': len(good_latency)/seconds,
              'quality_goodput_rps': sum(r['quality_pass'] is True for r in good_latency)/seconds
                                     if quality_complete else None,
              'quality_pass_rate': sum(r['quality_pass'] is True for r in success)/len(success)
                                   if quality_complete else None,
              'latency_samples': len(success), 'quality_evaluated': quality_complete,
              'output_tokens_p50': percentile(counts, .5)}
    for name in ('ttft_proxy_ms', 'e2e_ms', 'tpot_proxy_ms'):
        for q in (.5, .95):
            result[f'{name}_p{int(q*100)}'] = percentile([r[name] for r in success], q)
    return result


async def run_case(items, a, concurrency, repeat):
    queue = asyncio.Queue()
    for i in range(a.requests):
        queue.put_nowait((i, items[i % len(items)]))
    rows, warm_rows = [], []
    async with httpx.AsyncClient(timeout=None, trust_env=False,
                                 limits=httpx.Limits(max_connections=concurrency)) as client:
        async def worker(source, target, phase):
            while not source.empty():
                i, item = source.get_nowait()
                row = await request_once(client, a.base_url.rstrip('/')+'/chat/completions',
                    a.model, item, a.deadline, os.getenv('LLM_API_KEY', ''),
                    f'{a.label}-c{concurrency}-r{repeat}-{phase}-{i}', a.capture_text)
                target.append(row)
        warm_queue = asyncio.Queue()
        for i in range(a.warmup_requests):
            warm_queue.put_nowait((i, items[i % len(items)]))
        await asyncio.gather(*(worker(warm_queue, warm_rows, 'warm') for _ in range(concurrency)))
        if any(not r['ok'] for r in warm_rows):
            raise RuntimeError('representative warmup failed; fix readiness before measurement')
        # Representative shape/concurrency warmup is outside the measurement window.
        # With cache enabled it also warms the target prefix; cold runs forbid this option.
        start = time.perf_counter()
        await asyncio.gather(*(worker(queue, rows, 'measure') for _ in range(concurrency)))
        seconds = time.perf_counter()-start
    return rows, summarize(rows, seconds, a.ttft_slo_ms, a.e2e_slo_ms), warm_rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', default='http://127.0.0.1:8000/v1')
    p.add_argument('--model', default='teaching-mock')
    p.add_argument('--workload', type=Path, default=Path('workloads/smoke.jsonl'))
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--requests', type=int, default=24)
    p.add_argument('--concurrency', type=int, nargs='+', default=[1, 2, 4, 8])
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--warmup-requests', type=int, default=0,
                   help='representative requests per case before timing; warms KV too if enabled')
    p.add_argument('--deadline', type=float, default=60)
    p.add_argument('--ttft-slo-ms', type=float, required=True)
    p.add_argument('--e2e-slo-ms', type=float, required=True)
    p.add_argument('--mode', choices=['mock', 'shared', 'gpu'], default='mock')
    p.add_argument('--label', required=True)
    p.add_argument('--cache-state', choices=['cold', 'hot', 'mixed', 'disabled'], required=True)
    p.add_argument('--environment', type=Path)
    p.add_argument('--confirm-exclusive', action='store_true')
    p.add_argument('--capture-text', action='store_true', help='only for approved non-sensitive data')
    a = p.parse_args()
    if min([a.requests, a.repeats, a.deadline, a.ttft_slo_ms, a.e2e_slo_ms]+a.concurrency) <= 0:
        p.error('counts, concurrency, deadlines and SLOs must be positive')
    if any(not math.isfinite(x) for x in (a.deadline, a.ttft_slo_ms, a.e2e_slo_ms)):
        p.error('deadlines and SLOs must be finite')
    if a.warmup_requests < 0 or (a.cache_state == 'cold' and a.warmup_requests):
        p.error('warmup must be nonnegative; cold measurements cannot warm the target workload')
    if not a.base_url.startswith(('http://127.0.0.1:', 'http://localhost:', 'https://')):
        p.error('use loopback SSH tunnel or HTTPS; credentials must not cross plaintext networks')
    parsed_url = urlsplit(a.base_url)
    if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
        p.error('base URL must not contain credentials, query parameters or fragments')
    raw = a.workload.read_bytes()
    items = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if not items or len({x['id'] for x in items}) != len(items):
        p.error('workload must contain unique IDs')
    for item in items:
        if not item.get('messages') or type(item.get('max_tokens')) is not int or item['max_tokens'] < 1:
            p.error('each item requires messages and a positive max_tokens')
        quality_check('{}', item.get('quality'))  # validate rules before spending GPU time
    env = json.loads(a.environment.read_text()) if a.environment else {}
    if a.mode == 'gpu':
        required = ['gpu_name', 'gpu_uuid', 'model_revision', 'engine_version', 'student_commit', 'lease_id',
                    'image_digest', 'model_id']
        if not a.confirm_exclusive or any(not env.get(k) for k in required):
            p.error('gpu mode requires --confirm-exclusive and a filled --environment manifest')
        if env['model_id'] != a.model:
            p.error('manifest model_id must match --model')
    a.out.mkdir(parents=True, exist_ok=False)
    try:
        kit_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL,
                                             text=True).strip()
    except subprocess.SubprocessError:
        kit_commit = None
    metadata = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
                'mode': a.mode, 'exclusive_claim': a.confirm_exclusive, 'environment': env,
                'kit_commit': kit_commit, 'workload_sha256': hashlib.sha256(raw).hexdigest(),
                'load_model': 'closed_loop', 'cache_state_is_operator_declared': True,
                'config': {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()}}
    (a.out/'manifest.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False)+'\n')
    for repeat in range(1, a.repeats+1):
        for c in a.concurrency:
            rows, summary, warm_rows = asyncio.run(run_case(items, a, c, repeat))
            folder = a.out/f'c{c}-r{repeat}'
            folder.mkdir()
            (folder/'requests.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
            (folder/'warmup.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in warm_rows))
            summary.update({'concurrency': c, 'repeat': repeat})
            (folder/'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n')
            print(json.dumps(summary, ensure_ascii=False), flush=True)
    print('Saved:', a.out, '\nNext: python -m lab.report', a.out, '--out', a.out/'report.html')


if __name__ == '__main__':
    main()
