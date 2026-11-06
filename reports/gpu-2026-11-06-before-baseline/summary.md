# GPU 练习证据复核

状态：**INCOMPLETE**。不是官方评分；没有计算分数。
验收版本：`daytona-4090-v5-confirmed-5e2971c9`。

## 服务样本与原始数据复算

| 配置 | 负载/C | 样本/要求 | 成功/正确 | usage_verified | timing_complete | 数据复核 |
|---|---|---:|---:|---|---|---|
| baseline | short/1 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | short/4 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | short/8 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | short/16 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | long/1 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | long/4 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | long/8 | 0/48 | 0/0 | False | False | NOT_TESTED |
| baseline | long/16 | 0/48 | 0/0 | False | False | NOT_TESTED |
| optimized | short/1 | 48/48 | 48/48 | True | True | PASSED |
| optimized | short/4 | 48/48 | 48/48 | True | True | PASSED |
| optimized | short/8 | 48/48 | 48/48 | True | True | PASSED |
| optimized | short/16 | 48/48 | 48/48 | True | True | PASSED |
| optimized | long/1 | 48/48 | 48/48 | True | True | PASSED |
| optimized | long/4 | 48/48 | 48/48 | True | True | PASSED |
| optimized | long/8 | 48/48 | 48/48 | True | True | PASSED |
| optimized | long/16 | 48/48 | 48/48 | True | True | PASSED |

## Optimized 四组冻结阈值

| 负载/C | 指标 | 实测 | 要求 | 结果 |
|---|---|---:|---:|---|
| short/8 | output_tokens_per_second | 263.7802 | >= 71 | PASSED |
| short/8 | ttft_p95_ms | 220.8958 | <= 1780 | PASSED |
| short/8 | tpot_p95_ms | 20.8227 | <= 35 | PASSED |
| short/8 | e2e_p95_ms | 571.8077 | <= 2350 | PASSED |
| short/8 | quality_goodput | 14.6545 | >= 3.6700 | PASSED |
| short/8 | slo_attainment | 1.0000 | >= 0.9500 | PASSED |
| short/8 | success_rate | 1.0000 | >= 0.9900 | PASSED |
| short/8 | quality_rate | 1.0000 | >= 0.9900 | PASSED |
| short/16 | output_tokens_per_second | 392.4021 | >= 128 | PASSED |
| short/16 | ttft_p95_ms | 247.7794 | <= 1830 | PASSED |
| short/16 | tpot_p95_ms | 24.1624 | <= 40 | PASSED |
| short/16 | e2e_p95_ms | 746.8794 | <= 2600 | PASSED |
| short/16 | quality_goodput | 21.6747 | >= 6.6300 | PASSED |
| short/16 | slo_attainment | 1.0000 | >= 0.9500 | PASSED |
| short/16 | success_rate | 1.0000 | >= 0.9900 | PASSED |
| short/16 | quality_rate | 1.0000 | >= 0.9900 | PASSED |
| long/8 | output_tokens_per_second | 405.1529 | >= 254 | PASSED |
| long/8 | ttft_p95_ms | 293.2996 | <= 1870 | PASSED |
| long/8 | tpot_p95_ms | 18.9612 | <= 29 | PASSED |
| long/8 | e2e_p95_ms | 4983.2762 | <= 8900 | PASSED |
| long/8 | quality_goodput | 1.6189 | >= 0.9500 | PASSED |
| long/8 | slo_attainment | 1.0000 | >= 0.9500 | PASSED |
| long/8 | success_rate | 1.0000 | >= 0.9900 | PASSED |
| long/8 | quality_rate | 1.0000 | >= 0.9900 | PASSED |
| long/16 | output_tokens_per_second | 752.5386 | >= 485 | PASSED |
| long/16 | ttft_p95_ms | 464.5477 | <= 2070 | PASSED |
| long/16 | tpot_p95_ms | 19.7391 | <= 30 | PASSED |
| long/16 | e2e_p95_ms | 5315.1112 | <= 9350 | PASSED |
| long/16 | quality_goodput | 3.0066 | >= 1.8100 | PASSED |
| long/16 | slo_attainment | 1.0000 | >= 0.9500 | PASSED |
| long/16 | success_rate | 1.0000 | >= 0.9900 | PASSED |
| long/16 | quality_rate | 1.0000 | >= 0.9900 | PASSED |

## Optimized / baseline 比值

| 负载/C | 有效吞吐 | TTFT | TPOT | E2E | Goodput | 状态 |
|---|---:|---:|---:|---:|---:|---|
| short/1 | — | — | — | — | — | INCOMPLETE |
| short/4 | — | — | — | — | — | INCOMPLETE |
| short/8 | — | — | — | — | — | INCOMPLETE |
| short/16 | — | — | — | — | — | INCOMPLETE |
| long/1 | — | — | — | — | — | INCOMPLETE |
| long/4 | — | — | — | — | — | INCOMPLETE |
| long/8 | — | — | — | — | — | INCOMPLETE |
| long/16 | — | — | — | — | — | INCOMPLETE |

## 机制实验

### kv: PASSED

原始记录 12/12；manifest.complete=True。

| 分组 | 行数 | 实测均值/范围 |
|---|---:|---|
| cache_off / 128 | 3 | elapsed_ms: mean=921.4699, min=913.0665, max=936.1575; max_abs_error: mean=0.0001, min=0.0001, max=0.0001; top1_agreement: mean=1.0000, min=1.0000, max=1.0000; peak_allocated_bytes: mean=6398131200.0000, min=6398131200, max=6398131200; peak_reserved_bytes: mean=8038383616.0000, min=8038383616, max=8038383616 |
| cache_off / 512 | 3 | elapsed_ms: mean=1839.8849, min=1835.9457, max=1842.7839; max_abs_error: mean=0.0000, min=0.0000, max=0.0000; top1_agreement: mean=1.0000, min=1.0000, max=1.0000; peak_allocated_bytes: mean=6866795520.0000, min=6866795520, max=6866795520; peak_reserved_bytes: mean=12876513280.0000, min=12876513280, max=12876513280 |
| cache_on / 128 | 3 | elapsed_ms: mean=709.3248, min=700.9756, max=717.0187; max_abs_error: mean=0.0001, min=0.0001, max=0.0001; top1_agreement: mean=1.0000, min=1.0000, max=1.0000; peak_allocated_bytes: mean=6272634880.0000, min=6272634880, max=6272634880; peak_reserved_bytes: mean=6438256640.0000, min=6438256640, max=6438256640 |
| cache_on / 512 | 3 | elapsed_ms: mean=735.7004, min=732.0085, max=740.4528; max_abs_error: mean=0.0000, min=0.0000, max=0.0000; top1_agreement: mean=1.0000, min=1.0000, max=1.0000; peak_allocated_bytes: mean=6532785664.0000, min=6532785664, max=6532785664; peak_reserved_bytes: mean=6788481024.0000, min=6788481024, max=6788481024 |

### attention: PASSED

原始记录 18/18；manifest.complete=True。

| 分组 | 行数 | 实测均值/范围 |
|---|---:|---|
| flash / 128 | 3 | elapsed_ms: mean=0.0301, min=0.0283, max=0.0337; max_abs_error: mean=0.0078, min=0.0078, max=0.0078; peak_allocated_bytes: mean=9961472.0000, min=9961472, max=9961472; peak_reserved_bytes: mean=25165824.0000, min=25165824, max=25165824 |
| flash / 512 | 3 | elapsed_ms: mean=0.0406, min=0.0360, max=0.0439; max_abs_error: mean=0.0078, min=0.0078, max=0.0078; peak_allocated_bytes: mean=14286848.0000, min=14286848, max=14286848; peak_reserved_bytes: mean=52428800.0000, min=52428800, max=52428800 |
| flash / 1024 | 3 | elapsed_ms: mean=0.0367, min=0.0363, max=0.0377; max_abs_error: mean=0.0078, min=0.0078, max=0.0078; peak_allocated_bytes: mean=20054016.0000, min=20054016, max=20054016; peak_reserved_bytes: mean=119537664.0000, min=119537664, max=119537664 |
| math / 128 | 3 | elapsed_ms: mean=0.2651, min=0.2585, max=0.2708; max_abs_error: mean=0.0000, min=0.0000, max=0.0000; peak_allocated_bytes: mean=11470336.0000, min=11470336, max=11470336; peak_reserved_bytes: mean=25165824.0000, min=25165824, max=25165824 |
| math / 512 | 3 | elapsed_ms: mean=0.2718, min=0.2570, max=0.2898; max_abs_error: mean=0.0000, min=0.0000, max=0.0000; peak_allocated_bytes: mean=35262976.0000, min=35262976, max=35262976; peak_reserved_bytes: mean=52428800.0000, min=52428800, max=52428800 |
| math / 1024 | 3 | elapsed_ms: mean=0.3657, min=0.3651, max=0.3668; max_abs_error: mean=0.0000, min=0.0000, max=0.0000; peak_allocated_bytes: mean=101851648.0000, min=101851648, max=101851648; peak_reserved_bytes: mean=119537664.0000, min=119537664, max=119537664 |

### prefix: PASSED

原始记录 96/96；manifest.complete=True。

| 分组 | 行数 | 实测均值/范围 |
|---|---:|---|
| off / shared / cold | 24 | ttft_ms: mean=70.6298, min=56.9092, max=72.9420; e2e_ms: mean=88.2157, min=74.4634, max=90.3691; cached_tokens: mean=0.0000, min=0, max=0 |
| off / unique / cold | 24 | ttft_ms: mean=70.1836, min=57.2124, max=74.1442; e2e_ms: mean=87.6695, min=74.7723, max=89.9423; cached_tokens: mean=0.0000, min=0, max=0 |
| on / shared / cold | 3 | ttft_ms: mean=61.0245, min=58.5490, max=65.7199; e2e_ms: mean=78.4948, min=75.9988, max=83.1936; cached_tokens: mean=0.0000, min=0, max=0 |
| on / shared / hot | 21 | ttft_ms: mean=44.5875, min=40.8387, max=56.4072; e2e_ms: mean=61.9529, min=58.3886, max=73.4021; cached_tokens: mean=515.0000, min=515, max=515 |
| on / unique / cold | 24 | ttft_ms: mean=70.6980, min=57.8396, max=75.2056; e2e_ms: mean=88.4657, min=75.1825, max=94.1961; cached_tokens: mean=14.8750, min=0, max=17 |

## 证据边界

- Participant practice evidence; no official score or grader attestation.
- Correctness is rechecked against saved expected JSON with exact types; failures remain in denominators.
- Token provenance and usage_verified are checked against raw client records. Tokenizer is not run again; visible-token counts are the recorded client retokenization, not server-declared usage.
- Cell wall-clock windows come from saved benchmark summaries because raw rows do not retain absolute request timestamps; aggregates and percentiles are independently recomputed.
- PASSED threshold comparisons require complete samples, identity, timing and token-provenance checks. Missing data is NOT_TESTED or INCOMPLETE.
- Ratios require matching seeds and identical input files; null ratios are not interpreted as speedups.
- GPU work completion or release is not proved by these local report files.
