# 性能与机制：方法和当前证据

CPU 协议和事务验证已通过；没有 GPU 实测数据。以下配置为待测候选，所有 GPU 指标均 NOT_TESTED。

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

## KV Cache

缓存历史 K/V 避免解码每步重算完整历史，代价是与上下文长度近似线性增长的显存。独立实验固定 1.5B FP32，关闭 TF32：长度128/512 × cache on/off ×3轮，共12行；固定回放32个 continuation token。比较每个位置的 logits 最大绝对误差（≤0.001）和 top-1（100%）。CUDA 同步计时，CPU 拷贝不计入推理时间。

`每token KV字节=2(K,V)×layers×KV_heads×head_dim×dtype_bytes`；按实际模型配置计算单请求和八请求的 `(输入长度+32)` 容量。最终7B BF16与本实验1.5B FP32证据不能互换。

## RadixAttention / 前缀复用

跨请求共享前缀可复用预填充 KV。实际 SGLang 7B BF16：on/off × shared/unique ×3轮×8请求，共96行，前缀512 tokens。on/off 使用相同 token IDs；每组 flush_cache 保存状态。

关闭缓存必须命中0，共享热请求≥512，冷/不同前缀<512。命中取自引擎 meta_info，不从速度推断。简短OK任务仅用于机制证据，不能替代最终结构化负载。

## FlashAttention 与 FlashInfer

PyTorch 算子对照使用相同的合成 Q/K/V：B=1,H=8,dh=64，长度128/512/1024，BF16，causal，dropout=0。math/flash ×3轮，每轮10次，预热5次，共18行；最大误差≤0.02。保存6组 profiler 算子与压缩完整trace；后端不支持则失败，不静默回退。

此 PyTorch FlashAttention 实验与最终 SGLang 的 FlashInfer 是不同实现。最终配置和启动参数使用 FlashInfer，实际运行仍需 GPU 核验。

## 完整服务矩阵

官方 serving_benchmark：baseline/optimized × short/long × C=1/4/8/16 ×3轮×16请求，共768个测量请求，预热另记。chat-template 输入精确512/1024 tokens；JSON模式及中性解码不变，鉴权/配额/账本全程开启。

目标版本 `daytona-4090-v5-confirmed-5e2971c9`；四组全部八项同时达标，每组48请求，≥99%实际要求48/48成功且正确。本人吞吐、p95、goodput 目前无实测值，不能填参考分数。

## 溯源与局限

输出新目录；manifest先complete=false，完整运行后才true。完整矩阵数值失败仍保存全部数据并非零退出，中断保持false。JSON禁止NaN/Infinity，单个采集JSON≤256000字节；tensor用原始连续字节哈希，token数组按任务书JSON规则哈希。

尚未验证：新Daytona快照安装全部GPU锁、7B容量、300/480秒实验时限、性能SLO、鉴权/账本开销、真实GPU abort/显存释放。CPU强制终止验证在Windows进行，Linux SIGKILL待实际Linux环境验证。所有未运行结果保持NOT_TESTED。
