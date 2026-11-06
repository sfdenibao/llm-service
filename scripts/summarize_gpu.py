"""Local, read-only aggregation; never computes an official score.

python scripts/summarize_gpu.py --run-dir results/gpu-2026-11-06 \
  --acceptance tools/public-2026-11-06/acceptance.json --out reports/gpu-01
Optional --kv/--attention/--prefix override the corresponding run-dir subfolders.
The output directory must be new. Exit 0 means all required evidence passed;
exit 1 preserves a report containing failed, incomplete or untested evidence.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics


METRICS = (
    ('output_tokens_per_second', 'output_tokens_per_second_min', '>='),
    ('ttft_p95_ms', 'ttft_p95_ms_max', '<='), ('tpot_p95_ms', 'tpot_p95_ms_max', '<='),
    ('e2e_p95_ms', 'e2e_p95_ms_max', '<='), ('quality_goodput', 'quality_goodput_min', '>='),
    ('slo_attainment', 'slo_attainment_min', '>='), ('success_rate', 'success_rate_min', '>='),
    ('quality_rate', 'quality_rate_min', '>='),
)


def finite(value, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def p95(values):
    values = sorted(v for v in values if finite(v))
    return values[math.ceil(len(values) * .95) - 1] if values else None


def equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def quality(row, item):
    try:
        return row.get('ok') is True and item is not None and equal(json.loads(row['text']), item['expected'])
    except (ValueError, KeyError, TypeError):
        return False


class Reader:
    def __init__(self):
        self.hashes = {}
        self.errors = []

    def read(self, path, lines=False):
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()
            self.hashes[str(path)] = hashlib.sha256(data).hexdigest()
            def reject(value):
                raise ValueError('Nonfinite JSON value')
            text = data.decode('utf-8-sig')
            return ([json.loads(line, parse_constant=reject) for line in text.splitlines() if line.strip()]
                    if lines else json.loads(text, parse_constant=reject))
        except (ValueError, OSError) as error:
            self.errors.append({'file': str(path), 'error': type(error).__name__})
            return None


def serving(reader, directory, acceptance, config_hash):
    cells = []
    environment = reader.read(directory.parent / 'environment.json') or {}
    runtime = reader.read(directory.parent / 'info.json') or {}
    hardware_ok = (acceptance.get('hardware_contains', '') != '' and
                   acceptance['hardware_contains'] in environment.get('gpu', '') and
                   runtime.get('config', {}).get('dtype') == acceptance.get('dtype') and
                   runtime.get('mode') == 'live' and runtime.get('engine') == 'sglang')
    repeats, count = acceptance['repeats'], acceptance['requests_per_repeat']
    targets = {(t['workload'], t['concurrency']): t for t in acceptance['targets']}
    manifests = {}
    for profile in ('baseline', 'optimized'):
        raw = reader.read(directory / f'{profile}-serving-raw.jsonl', True) or []
        summaries = reader.read(directory / f'{profile}-serving-summary.json') or []
        manifest = reader.read(directory / f'{profile}-benchmark-manifest.json') or {}
        identity = reader.read(directory / f'{profile}-serving-identity.json') or {}
        manifests[profile] = manifest
        identity_ok = (hardware_ok and acceptance.get('status') == 'calibrated' and
                       manifest.get('config_sha256') == config_hash and manifest.get('acceptance') == acceptance
                       and all(identity.get(k) == acceptance[k] for k in ('model_id', 'model_revision', 'decoding_policy'))
                       and identity.get('profile') == profile)
        for spec in acceptance['workloads']:
            workload = spec['id']
            for concurrency in acceptance['concurrencies']:
                rows = [r for r in raw if (r.get('profile'), r.get('workload'), r.get('concurrency')) == (profile, workload, concurrency)]
                matches = [s for s in summaries if (s.get('profile'), s.get('workload'), s.get('concurrency')) == (profile, workload, concurrency)]
                summary = matches[0] if len(matches) == 1 else {}
                inputs = {}
                inputs_complete = True
                for repeat in range(repeats):
                    values = reader.read(directory / f'{profile}-{workload}-c{concurrency}-r{repeat}-inputs.json')
                    if not isinstance(values, list) or len(values) != count:
                        inputs_complete = False
                        continue
                    for item in values:
                        inputs[(repeat, item['id'])] = item
                complete = (len(rows) == repeats * count and len({r.get('id') for r in rows}) == len(rows)
                            and Counter(r.get('repeat') for r in rows) == Counter({i: count for i in range(repeats)})
                            and inputs_complete and len(inputs) == repeats * count and len(matches) == 1
                            and {(r.get('repeat'), r.get('workload_id')) for r in rows} == set(inputs))
                successful = [r for r in rows if r.get('ok') is True and r.get('status') == 200 and finite(r.get('output_tokens'), True)]
                verified_quality = [r for r in successful if quality(r, inputs.get((r.get('repeat'), r.get('workload_id'))))]
                quality_flags_match = all(r.get('quality_pass') is quality(r, inputs.get((r.get('repeat'), r.get('workload_id')))) for r in rows)
                usage_verified = bool(rows) and all(type(r.get('output_tokens')) is int and r['output_tokens'] >= 0
                    and r.get('token_count_source') == 'controller_retokenized_visible_text' and r.get('input_tokens') == spec['input_tokens'] for r in rows)
                timing_complete = bool(verified_quality) and all(all(finite(r.get(k)) for k in ('ttft_ms', 'tpot_ms', 'e2e_ms'))
                    and r.get('content_events', 0) > 1 for r in verified_quality)
                seconds = summary.get('window_seconds')
                metrics = {'requests': len(rows), 'successful': len(successful), 'quality_pass': len(verified_quality),
                    'success_rate': len(successful) / len(rows) if rows else None,
                    'quality_rate': len(verified_quality) / len(rows) if rows else None,
                    'window_seconds': seconds, 'usage_verified': usage_verified, 'timing_complete': timing_complete,
                    'output_tokens_per_second': sum(r['output_tokens'] for r in verified_quality) / seconds if finite(seconds, True) else None,
                    'quality_goodput': None, 'slo_attainment': None}
                for field in ('ttft', 'tpot', 'e2e'):
                    metrics[field + '_p95_ms'] = p95(r.get(field + '_ms') for r in verified_quality)
                target = targets.get((workload, concurrency))
                if target and rows and finite(seconds, True):
                    good = [r for r in verified_quality if all(finite(r.get(field + '_ms')) and
                            r[field + '_ms'] <= target[field + '_p95_ms_max'] for field in ('ttft', 'tpot', 'e2e'))]
                    metrics.update(quality_goodput=len(good) / seconds, slo_attainment=len(good) / len(rows))
                mismatch = []
                for key, actual in metrics.items():
                    declared = summary.get(key)
                    agrees = math.isclose(actual, declared, rel_tol=1e-9, abs_tol=1e-9) if finite(actual) and finite(declared) else actual == declared
                    if summary and not agrees:
                        mismatch.append(key)
                contract_ok = (identity_ok and summary.get('workload_version') == acceptance['workload_version']
                    and summary.get('measurement_version') == acceptance['measurement_version']
                    and summary.get('decoding_policy') == acceptance['decoding_policy']
                    and summary.get('response_format') == acceptance.get('response_format')
                    and summary.get('input_tokens') == spec['input_tokens']
                    and summary.get('max_output_tokens_requested') == spec['max_output_tokens'])
                # Baseline is a negative control: genuine timeout/quality failures remain valid
                # measurements, including a cell with no successful latency samples.
                timing_gate = timing_complete if profile == 'optimized' else True
                state = ('NOT_TESTED' if not rows else 'INCOMPLETE' if not complete or not finite(seconds, True)
                         else 'PASSED' if contract_ok and quality_flags_match and usage_verified and timing_gate and not mismatch else 'FAILED')
                cells.append({'profile': profile, 'workload': workload, 'concurrency': concurrency,
                    'status': state, 'expected_requests': repeats * count, 'complete_samples': complete,
                    'contract_identity_matches': contract_ok, 'quality_flags_match_recheck': quality_flags_match,
                    'failed_requests': len(rows) - len(successful),
                    'error_counts': dict(Counter(str(r.get('error') or 'quality_failed') for r in rows if r not in verified_quality)),
                    'declared_summary_mismatches': mismatch, 'metrics': metrics})
    comparisons = []
    for workload in ('short', 'long'):
        for concurrency in (8, 16):
            target = targets.get((workload, concurrency), {})
            cell = next((c for c in cells if (c['profile'], c['workload'], c['concurrency']) == ('optimized', workload, concurrency)), None)
            checks = []
            for metric, threshold, comparison in METRICS:
                actual = cell['metrics'].get(metric) if cell else None
                limit = target.get(threshold)
                status = 'NOT_TESTED' if not cell or cell['status'] == 'NOT_TESTED' else 'INCOMPLETE'
                if cell and cell['status'] == 'PASSED' and finite(actual) and finite(limit, True):
                    status = 'PASSED' if (actual >= limit if comparison == '>=' else actual <= limit) else 'FAILED'
                elif cell and cell['status'] == 'FAILED':
                    status = 'FAILED'
                checks.append({'metric': metric, 'actual': actual, 'threshold': limit, 'comparison': comparison, 'status': status})
            statuses = {c['status'] for c in checks}
            state = 'PASSED' if statuses == {'PASSED'} else ('FAILED' if 'FAILED' in statuses else 'NOT_TESTED' if statuses == {'NOT_TESTED'} else 'INCOMPLETE')
            comparisons.append({'workload': workload, 'concurrency': concurrency, 'status': state, 'checks': checks})
    ratios = []
    same_seed = manifests['baseline'].get('seed') is not None and manifests['baseline'].get('seed') == manifests['optimized'].get('seed')
    for b in (c for c in cells if c['profile'] == 'baseline'):
        o = next(c for c in cells if (c['profile'], c['workload'], c['concurrency']) == ('optimized', b['workload'], b['concurrency']))
        matched_inputs = all((reader.read(directory / f'baseline-{b["workload"]}-c{b["concurrency"]}-r{r}-inputs.json') is not None and
                             reader.read(directory / f'baseline-{b["workload"]}-c{b["concurrency"]}-r{r}-inputs.json') ==
                             reader.read(directory / f'optimized-{b["workload"]}-c{b["concurrency"]}-r{r}-inputs.json')) for r in range(repeats))
        ready = same_seed and matched_inputs and all(c['status'] == 'PASSED' for c in (b, o))
        values = {metric: o['metrics'][metric] / b['metrics'][metric] if ready and finite(b['metrics'].get(metric), True)
                  and finite(o['metrics'].get(metric)) else None
                  for metric in ('output_tokens_per_second', 'ttft_p95_ms', 'tpot_p95_ms', 'e2e_p95_ms', 'quality_goodput')}
        ratios.append({'workload': b['workload'], 'concurrency': b['concurrency'], 'same_seed': same_seed,
            'identical_inputs': matched_inputs, 'status': 'PASSED' if ready else 'INCOMPLETE',
            'definition': 'optimized / baseline; throughput >1 is faster, latency <1 is faster', 'ratios': values})
    return {'archived_hardware_and_dtype_match': hardware_ok, 'cells': cells, 'targets': comparisons, 'profile_ratios': ratios}


def mechanism(reader, directory, case):
    manifest = reader.read(directory / 'manifest.json') or {}
    rows = reader.read(directory / 'raw.jsonl', True) or []
    summary = reader.read(directory / 'summary.json') or {}
    expected = {'kv': 12, 'attention': 18, 'prefix': 96}[case]
    groups = defaultdict(list)
    numeric_ok = bool(rows)
    missing_aux = []
    expected_identity = {
        'kv': ('Qwen/Qwen2.5-1.5B-Instruct', '989aa7980e4cf806f80c7fef2b1adb7bc71aa306', 'float32'),
        'attention': ('synthetic-QKV', None, 'bfloat16'),
        'prefix': ('Qwen/Qwen2.5-7B-Instruct', 'a09a35458c702b33eeacc393d103063234e8bc28', 'bfloat16'),
    }[case]
    identity_ok = (manifest.get('case') == case and
        (manifest.get('model'), manifest.get('revision'), manifest.get('parameters', {}).get('dtype')) == expected_identity
        and 'RTX 4090' in (manifest.get('gpu') or '') and bool(manifest.get('cuda')))
    if case == 'prefix':
        expected_keys = {(v, g, r, i) for v in ('off', 'on') for g in ('shared', 'unique') for r in range(3) for i in range(8)}
        keys = [(r.get('variant'), r.get('group'), r.get('repeat'), r.get('index')) for r in rows]
        for row in rows:
            cached = row.get('cached_tokens')
            cache_ok = type(cached) is int and (cached == 0 if row.get('variant') == 'off' else
                cached >= 512 if row.get('group') == 'shared' and row.get('index', 0) > 0 else cached < 512)
            numeric_ok = numeric_ok and row.get('transport_ok') is True and row.get('output', '').strip() == 'OK' and cache_ok
            state = 'hot' if row.get('variant') == 'on' and row.get('group') == 'shared' and row.get('index', 0) > 0 else 'cold'
            groups[(row.get('variant'), row.get('group'), state)].append(row)
        summary_ok = summary.get('cache_and_output_passed') is True
    else:
        variants = ('cache_off', 'cache_on') if case == 'kv' else ('math', 'flash')
        lengths = (128, 512) if case == 'kv' else (128, 512, 1024)
        expected_keys = {(v, n, r) for v in variants for n in lengths for r in range(3)}
        keys = [(r.get('variant'), r.get('length'), r.get('repeat')) for r in rows]
        for row in rows:
            numeric_ok = numeric_ok and finite(row.get('max_abs_error')) and row['max_abs_error'] <= (.001 if case == 'kv' else .02)
            if case == 'kv':
                numeric_ok = numeric_ok and row.get('top1_agreement') == 1 and row.get('dtype') == 'float32'
            groups[(row.get('variant'), row.get('length'))].append(row)
        summary_ok = summary.get('numerical_passed' if case == 'kv' else 'numerical_and_backend_passed') is True
        if case == 'attention' and rows:
            for length in lengths:
                for variant in variants:
                    operators = reader.read(directory / f'{variant}-{length}-operators.json')
                    if operators is None or not (directory / f'{variant}-{length}.trace.gz').is_file():
                        missing_aux.append(f'{variant}-{length} profiler evidence')
                    elif variant == 'flash':
                        numeric_ok = numeric_ok and any('flash_attention' in op for op in operators)
    measurements = []
    for key, values in sorted(groups.items()):
        measurement = {'group': list(key), 'rows': len(values)}
        for field in ('elapsed_ms', 'ttft_ms', 'e2e_ms', 'cached_tokens', 'max_abs_error',
                      'top1_agreement', 'peak_allocated_bytes', 'peak_reserved_bytes'):
            numbers = [r[field] for r in values if finite(r.get(field))]
            if numbers:
                measurement[field] = {'mean': statistics.fmean(numbers), 'min': min(numbers), 'max': max(numbers)}
        measurements.append(measurement)
    complete = manifest.get('complete') is True and len(rows) == expected and len(set(keys)) == expected and set(keys) == expected_keys and bool(summary) and not missing_aux
    state = 'NOT_TESTED' if not manifest and not rows else 'INCOMPLETE' if not complete else 'PASSED' if numeric_ok and summary_ok and identity_ok else 'FAILED'
    return {'case': case, 'status': state, 'rows': len(rows), 'expected_rows': expected,
        'manifest_complete': manifest.get('complete'), 'manifest': manifest, 'matrix_complete': set(keys) == expected_keys,
        'numerical_or_cache_recheck_passed': numeric_ok, 'identity_matches': identity_ok, 'missing_auxiliary_evidence': missing_aux,
        'measurements': measurements, 'original_summary': summary}


def display(value):
    return f'{value:.4f}' if isinstance(value, float) else '—' if value is None else str(value)


def markdown(report):
    lines = ['# GPU 练习证据复核', '', f"状态：**{report['status']}**。不是官方评分；没有计算分数。",
        f"验收版本：`{report['acceptance_version']}`。", '', '## 服务样本与原始数据复算', '',
        '| 配置 | 负载/C | 样本/要求 | 成功/正确 | 成功率/正确率 | usage_verified | timing_complete | 数据复核 |',
        '|---|---|---:|---:|---:|---|---|---|']
    for c in report['serving']['cells']:
        m = c['metrics']
        rates = f"{m['success_rate']:.2%}/{m['quality_rate']:.2%}" if m['requests'] else '—'
        lines.append(f"| {c['profile']} | {c['workload']}/{c['concurrency']} | {m['requests']}/{c['expected_requests']} | {m['successful']}/{m['quality_pass']} | {rates} | {m['usage_verified']} | {m['timing_complete']} | {c['status']} |")
    lines += ['', '“数据复核 PASSED”表示记录完整且复算一致，不表示该行全部请求成功。baseline 失败保留在成功率和正确率分母中；冻结阈值仅判定下表四组 optimized。', '']
    for c in report['serving']['cells']:
        if c['error_counts']:
            lines.append(f"- {c['profile']} {c['workload']}/C{c['concurrency']} 错误分布：{json.dumps(c['error_counts'], ensure_ascii=False)}")
    lines += ['', '## Optimized 四组冻结阈值', '', '| 负载/C | 指标 | 实测 | 要求 | 结果 |', '|---|---|---:|---:|---|']
    for cell in report['serving']['targets']:
        for c in cell['checks']:
            lines.append(f"| {cell['workload']}/{cell['concurrency']} | {c['metric']} | {display(c['actual'])} | {c['comparison']} {display(c['threshold'])} | {c['status']} |")
    lines += ['', '## Optimized / baseline 比值', '', '| 负载/C | 有效吞吐 | TTFT | TPOT | E2E | Goodput | 状态 |', '|---|---:|---:|---:|---:|---:|---|']
    for r in report['serving']['profile_ratios']:
        lines.append('| ' + f"{r['workload']}/{r['concurrency']} | " + ' | '.join(display(v) for v in r['ratios'].values()) + f" | {r['status']} |")
    lines += ['', '## 机制实验', '']
    for m in report['mechanisms']:
        lines += [f"### {m['case']}: {m['status']}", '', f"原始记录 {m['rows']}/{m['expected_rows']}；manifest.complete={m['manifest_complete']}。", '',
            '| 分组 | 行数 | 实测均值/范围 |', '|---|---:|---|']
        for g in m['measurements']:
            actual = '; '.join(f"{k}: mean={display(v['mean'])}, min={display(v['min'])}, max={display(v['max'])}" for k, v in g.items() if isinstance(v, dict))
            lines.append(f"| {' / '.join(map(str, g['group']))} | {g['rows']} | {actual} |")
        lines.append('')
    lines += ['## 证据边界', ''] + ['- ' + s for s in report['limitations']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    for case in ('kv', 'attention', 'prefix'):
        parser.add_argument('--' + case, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    reader = Reader()
    acceptance = reader.read(args.acceptance)
    if not acceptance:
        raise SystemExit('Cannot read frozen acceptance configuration')
    digest = reader.hashes[str(args.acceptance)]
    report = {'schema': 'llm-gpu-local-summary/1', 'created_at': datetime.now(timezone.utc).isoformat(),
        'official_score': False, 'acceptance_version': acceptance['version'], 'acceptance_sha256': digest,
        'serving': serving(reader, args.run_dir / 'serving', acceptance, digest),
        'mechanisms': [mechanism(reader, getattr(args, case) or args.run_dir / case, case) for case in ('kv', 'attention', 'prefix')],
        'limitations': ['Participant practice evidence; no official score or grader attestation.',
            'Correctness is rechecked against saved expected JSON with exact types; failures remain in denominators.',
            'Baseline is a negative control: real failures do not fail the evidence-integrity check. Only optimized short/long C8/C16 are compared with the 32 frozen thresholds.',
            'Token provenance and usage_verified are checked against raw client records. Tokenizer is not run again; visible-token counts are the recorded client retokenization, not server-declared usage.',
            'Cell wall-clock windows come from saved benchmark summaries because raw rows do not retain absolute request timestamps; aggregates and percentiles are independently recomputed.',
            'PASSED threshold comparisons require complete samples, identity, timing and token-provenance checks. Missing data is NOT_TESTED or INCOMPLETE.',
            'Ratios require matching seeds and identical input files; null ratios are not interpreted as speedups.',
            'GPU work completion or release is not proved by these local report files.']}
    commit_file = args.run_dir / 'benchmark-tested-commit.txt'
    report['benchmark_commit'] = commit_file.read_text().strip() if commit_file.is_file() else None
    report['source_files_sha256'] = reader.hashes
    report['read_errors'] = reader.errors
    states = [c['status'] for c in report['serving']['cells']] + [c['status'] for c in report['serving']['targets']] + [m['status'] for m in report['mechanisms']]
    report['status'] = 'FAILED' if reader.errors or 'FAILED' in states else 'PASSED' if states and all(s == 'PASSED' for s in states) else 'INCOMPLETE'
    (args.out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.out / 'summary.md').write_text(markdown(report), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'official_score': False,
        'targets': [{k: c[k] for k in ('workload', 'concurrency', 'status')} for c in report['serving']['targets']],
        'mechanisms': [{k: m[k] for k in ('case', 'status', 'rows')} for m in report['mechanisms']]}))
    return 0 if report['status'] == 'PASSED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
