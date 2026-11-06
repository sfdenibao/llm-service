# 性能与机制：方法和当前证据

2026-11-06 已在一台 Daytona RTX 4090 上完成真实服务、baseline/optimized 全矩阵与三项机制实验。两配置各384个测量请求，baseline成功且正确333/384，optimized为384/384；optimized四个目标组的32项公开阈值全部通过。两配置使用同GPU、同seed `1093552657`，24份对应输入文件逐字节一致。第一次正式评测随后因 cuDNN 下载超时在安装阶段终止（自动0/95，GPU阶段未执行），见[安装修复记录](install-recovery.md)；本文保留此前练习数据。[完整复核报告](../reports/gpu-2026-11-06-final/summary.md) 的 PASSED 表示证据完整、复算一致及optimized公开阈值达标，不能解读为baseline没有失败。

基准与机制实验固定提交 `837ca04e52fe800f974cf46f44b7602c87012bb6`，原始记录位于 [gpu-2026-11-06](../results/gpu-2026-11-06/)。环境为 RTX 4090（23028 MiB）、驱动 580.178.04、CUDA 12.8、PyTorch 2.9.1、SGLang 0.5.9、Transformers 4.57.1。最终模型为固定 revision 的 Qwen2.5-7B-Instruct BF16；KV 对照使用独立 1.5B FP32。

| 配置 | baseline | optimized |
| --- | --- | --- |
| 引擎并行请求上限 | 1 | 32 |
| KV token 预算 | 4096 | 16384 |
| 静态显存比例 | 0.70 | 0.80 |
| Radix 缓存 | 关闭 | 开启 |
| Attention 后端 | FlashInfer | FlashInfer |
| 网关 active / waiting | 1 / 24 | 32 / 64 |

网关按 FIFO 准入并立即提交；每轮参与计算的序列由 SGLang 连续批处理安排。两层职责不同，网关不凑批等待。

## SSE 与延迟

网络块、SSE 事件、模型 token 是不同粒度。首 token 只计非空内容，role/usage 不计。`TTFT=t_first−t0`；`E2E=t_end−t0`；存在多个内容事件且 N>1 时 `TPOT≈(t_last−t_first)/(N−1)`，它不是逐 token ITL。有效吞吐为正确成功输出 token 总数除以整组窗口，失败必须保留在分母。Goodput/SLO 按冻结配置计算。

CPU 信号测试在释放上游结束信号之前收到第一段中文，同时验证 UTF-8 拆分和 SSE 合包。曾发现 AnyIO 重复取消中断清理，改用 AnyIO shield 后 abort、账本和槽位均通过。这是生命周期证据，不证明 GPU 计算停止。

本次 [真实服务取消探针](../results/gpu-2026-11-06/live-cancel-01.json) 7/7 通过：收到首段内容后主动断开，约 5.081 秒内 active/queued 与预占归零，未知 usage 保持 null，后续请求成功且精确记账。`gpu_task_release_proven=false` 明确保留：HTTP、账本恢复与 abort 路径不能证明 GPU 内部任务或显存立即释放。

## KV Cache

缓存历史 K/V 避免解码每步重算完整历史，代价是与上下文长度近似线性增长的显存。独立实验固定 1.5B FP32，关闭 TF32：长度128/512 × cache on/off ×3轮，共12行；固定回放32个 continuation token。比较每个位置的 logits 最大绝对误差（≤0.001）和 top-1（100%）。CUDA 同步计时，CPU 拷贝不计入推理时间。

`每token KV字节=2(K,V)×layers×KV_heads×head_dim×dtype_bytes`；按实际模型配置计算单请求和八请求的 `(输入长度+32)` 容量。最终7B BF16与本实验1.5B FP32证据不能互换。

实测 12 行完整，数值比较通过：最大绝对误差 `5.5313e-5`，top-1 一致率 100%。512-token 输入、续写 32 tokens 的三轮中位耗时从 cache_off 的 1840.925 ms 降至 cache_on 的 734.640 ms，约 2.506 倍加速；128-token 输入为 915.186 → 709.980 ms。历史越长，避免重复计算的收益越明显。这是固定回放的解码实验，不是最终 7B 服务吞吐。原始记录与计算容量见 [KV 结果](../results/gpu-2026-11-06/kv/)。

## RadixAttention / 前缀复用

跨请求共享前缀可复用预填充 KV。实际 SGLang 7B BF16：on/off × shared/unique ×3轮×8请求，共96行，前缀512 tokens。on/off 使用相同 token IDs；每组 flush_cache 保存状态。

关闭缓存必须命中0，共享热请求≥512，冷/不同前缀<512。命中取自引擎 meta_info，不从速度推断。简短OK任务仅用于机制证据，不能替代最终结构化负载。

实测 96 行全部通过：关闭缓存时 cached_tokens 为 0；开启后共享热请求均为 515，冷共享请求为 0，不同前缀为 0–17，满足上述条件。共享组正确成功请求数/秒的三轮中位数从 11.3485 提升至 15.6174，约 1.376 倍；不同前缀没有相同收益。这与“复用共同开头的预填充结果”相符，并由真实 cached_tokens 支持。记录见 [prefix 结果](../results/gpu-2026-11-06/prefix/)，该 goodput 没有使用最终服务 SLO，不能与后文直接混算。

## FlashAttention 与 FlashInfer

PyTorch 算子对照使用相同的合成 Q/K/V：B=1,H=8,dh=64，长度128/512/1024，BF16，causal，dropout=0。math/flash ×3轮，每轮10次，预热5次，共18行；最大误差≤0.02。保存6组 profiler 算子与压缩完整trace；后端不支持则失败，不静默回退。

实测 18 行全部通过，最大绝对误差 0.0078125，低于 0.02；6 组 profiler 算子文件与压缩 trace 已保存。长度 1024 时三轮中位耗时 math 为 0.3652 ms、flash 为 0.0363 ms。这说明在本次合成张量上融合计算与减少中间访存有收益，不能外推为整套服务同比例加速。见 [attention 结果](../results/gpu-2026-11-06/attention/)。

此 PyTorch FlashAttention 实验与最终 SGLang 的 FlashInfer 是不同实现。最终服务的实际 `/info` 与运行配置使用 FlashInfer，不能把 PyTorch profiler 证据当作 SGLang 全链路算子追踪。

## 完整服务矩阵

官方 serving_benchmark：baseline/optimized × short/long × C=1/4/8/16 ×3轮×16请求，共768个测量请求，预热另记。chat-template 输入精确512/1024 tokens；JSON模式及中性解码不变，鉴权/配额/账本全程开启。

目标版本 `daytona-4090-v5-confirmed-5e2971c9`；四组全部八项同时达标，每组48请求，≥99%实际要求48/48成功且正确。当前 optimized 的 short/long × C=1/4/8/16 共8组、384个测量请求均成功且正确，预热另存。以下为公开练习负载实测，数值格式为“实测 / 门槛”：

| 目标组 | 输出 token/s（≥） | TTFT p95 ms（≤） | TPOT p95 ms（≤） | E2E p95 ms（≤） | 质量 goodput req/s（≥） |
| --- | --- | --- | --- | --- | --- |
| short C8 | 263.78 / 71 | 220.90 / 1780 | 20.82 / 35 | 571.81 / 2350 | 14.654 / 3.67 |
| short C16 | 392.40 / 128 | 247.78 / 1830 | 24.16 / 40 | 746.88 / 2600 | 21.675 / 6.63 |
| long C8 | 405.15 / 254 | 293.30 / 1870 | 18.96 / 29 | 4983.28 / 8900 | 1.619 / 0.95 |
| long C16 | 752.54 / 485 | 464.55 / 2070 | 19.74 / 30 | 5315.11 / 9350 | 3.007 / 1.81 |

四组的 SLO 达成率均为 1.00（门槛 0.95），成功率和质量正确率均为 1.00（各自门槛 0.99），连同表中5项共32项通过。C1/C4没有配置相应目标，其 SLO/goodput 为 null，保留原样。完整数据见 [optimized 汇总](../results/gpu-2026-11-06/serving/optimized-serving-summary.json) 与 [baseline 汇总](../results/gpu-2026-11-06/serving/baseline-serving-summary.json)，同目录保留 raw、warmup、输入及 manifest。

baseline将网关和引擎同时限制为单请求执行，增加并发后其长输出吞吐仍约55 token/s，等待主要发生在队列中；optimized让引擎连续批处理多个请求，四目标的有效吞吐分别达到baseline的5.654、8.479、7.316、13.625倍。表中箭头为baseline → optimized，延迟单位ms：

| 目标组 | 成功且正确/48 | 有效输出 token/s | TTFT p95 | E2E p95 | SLO达成率 |
| --- | --- | --- | --- | --- | --- |
| short C8 | 48 → 48 | 46.65 → 263.78 | 2793.72 → 220.90 | 3118.37 → 571.81 | 31.25% → 100% |
| short C16 | 48 → 48 | 46.28 → 392.40 | 5853.24 → 247.78 | 6179.05 → 746.88 | 31.25% → 100% |
| long C8 | 30 → 48 | 55.38 → 405.15 | 18247.16 → 293.30 | 22668.55 → 4983.28 | 6.25% → 100% |
| long C16 | 15 → 48 | 55.23 → 752.54 | 18310.84 → 464.55 | 22734.91 → 5315.11 | 6.25% → 100% |

baseline其余6组均48/48成功且正确。51个失败全部是HTTP504：long/C8为18个、long/C16为33个；原始行均没有内容事件、TTFT为null，E2E为20.033–20.168秒，服务计数器亦记录51次timed_out。结合单执行槽、20秒排队上限与 `Admission.acquire` 的504路径，这些记录符合排队超时瓶颈；客户端raw未保存504响应体，不能据此声称模型输出错误或显存不足。所有失败保留在成功率、正确率与SLO分母中，不按成功样本重置分母。

吞吐收益不意味着每个token都更快：四个目标组的TPOT相对baseline为1.186、1.377、1.078、1.122倍，略变慢但仍在阈值内；总吞吐、排队等待及完成率明显改善。C1的吞吐只提高约1.063倍（short）和1.014倍（long），也支持主要收益来自并发处理。由于同时调整网关槽位、引擎并发、KV预算与prefix，不能把整套服务收益归给某一个开关；独立机制实验负责隔离各自作用。

## 溯源与局限

输出新目录；manifest先complete=false，完整运行后才true。完整矩阵数值失败仍保存全部数据并非零退出，中断保持false。JSON禁止NaN/Infinity，单个采集JSON≤256000字节；tensor用原始连续字节哈希，token数组按任务书JSON规则哈希。

本次首次部署确实遇到 Python.h 缺失与 venv 中 ninja 不在 PATH 的问题，前两次失败日志保留。补齐原生构建依赖、传递 venv PATH 后服务启动；随后发现真实 SGLang 返回可为 null 的 cache usage 细项，修正解析并增加回归后通过真实服务自测。对应源码修复已提交。已有环境上 setup 重跑耗时 1.329 秒且 PID 保持，只证明幂等；后续安装修复后的 `5d1d2ee` 已在新实例、新虚拟环境323秒启动，见[安装恢复](install-recovery.md)；平台模型和wheel缓存保留。

Ubuntu Actions 的 CPU 回归现为 24/24，含受控崩溃恢复；真实服务自测5项、管理与真实 Agent自测6组、HTTP取消恢复7项已通过。管理自测中的4类故障注入仍由CPU测试覆盖，不表示全部已做GPU故障注入。完整服务基准全程保留鉴权、额度与账本，但未单独测量它们的净开销。首次正式评测已在安装阶段终止；第二台全新环境重建已通过，GPU内部单请求任务/显存释放证明仍未完成。

本地复核按保存的expected JSON重新检查值与精确类型，按raw重新计算计数和分位数；可见token数沿用压测客户端已记录的重分词结果，没有再次加载tokenizer复算。raw不含绝对请求时间，整组窗口沿用保存的benchmark summary。这些证据足以支持本次公开练习比较，不能代替独立正式评测。

结束前恢复optimized，实际进程参数与SGLang原生metadata一致：max-running-requests=32、KV预算16384、mem_fraction=0.8、FlashInfer、prefix开启。最后基础自测5/5，账本reserved金额/tokens均为0；累计账本含预热、自测与未知用量记录，不能当作768条性能样本账本逐条对应。147个导出文件对三项运行时凭据精确值扫描通过。2026-11-06 20:53:08（Asia/Shanghai）停止本项目进程后，进程清单为空、gateway.pid不存在、GPU单次快照1 MiB/0%。这是整个服务停止后的观察，不补作此前单请求abort的GPU释放证明；平台练习实例当时仍allocated，回收状态单独记录。见同目录 `optimized-runtime-audit.json`、`selftest-final.json`、`benchmark-billing-summary.json`、`credential-scan.json`、`service-stop.json`。
