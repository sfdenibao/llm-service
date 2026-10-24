# 需求—模块—测试对应表

依据：`taskbook-2026-10-24.md`，评分 `llm-lab/5.0`、服务 `llm-lab/1.0`、管理 `credential-service/1`。下列是验收目标；当前实现映射见 README，实测状态见 test-results.md。管理事务模块集中于 control_plane/storage.py，保持原子事务边界。行号对应保留的任务书副本。

| 需求 | 目标模块 / 入口 | 必须验证的行为 | 证据层次 / 来源行号 |
| --- | --- | --- | --- |
| 从空目录重建和幂等启动 | `setup.sh`、依赖锁、`server/supervisor.py` | 安装后后台启动；健康就绪才退出 0；同配置不重复启动；切 profile 只停止自己的进程；保留 DATA_DIR；真实监听 PID | Linux 部署；272–291、797–848 |
| CPU 合同模式 | `setup.sh --contract-test` | 使用外部 ENGINE_BASE_URL；不安装 CUDA/SGLang/权重；鉴权、数据库、配额仍是真实代码 | 18 项软评测；674–689 |
| 基础接口与鉴权 | `server/app.py` | `/healthz` 准确反映引擎状态；`/info`、`/v1/models`、`/metrics` 鉴权；未知 key 拒绝；不暴露内部管理路由 | CPU + GPU；850–900 |
| 引擎适配 | `server/engine.py` | model/revision 固定；JSON 模式原样传递；同 request_id 追踪与 abort；实际版本/配置可核对 | CPU fixture + GPU；850–900 |
| SSE | `server/streaming.py` | 边读边发；分块/合包/中文 UTF-8；finish、usage、DONE 完整；流内错误不伪装成功 | 信号控制的真实 HTTP 测试 + GPU 时间线；902–1042 |
| 有界排队与释放 | `server/admission.py`、`server/metrics.py` | active/queued 有界；满载 429 + Retry-After；排队/执行超时；取消传递；finally 恰好释放；拒绝原因计数 | CPU 并发故障测试 + GPU 恢复探针；74–125、895–900 |
| 用户和密钥生命周期 | `control_plane/api.py`、`keys.py` | 两租户各两 key；32 随机字节；摘要保存；仅签发/轮换展示原文；过期/撤销 401、禁用 403；零宽限原子轮换 | CPU + 源码审查 + GPU；140–156、195–218 |
| 权限隔离 | `control_plane/api.py` | ADMIN_API_KEY 独立；普通 key 不能调用七个管理写接口；拒绝前不能发生写入；用户身份由服务端确定 | CPU 状态前后对比 + GPU；195–218、607–619 |
| 计费和预占 | `control_plane/billing.py`、`usage.py` | 同一事务检查余额并预占；整数 micro-USD；引擎真实 usage；100/50 按 2/6 结算 500；include_usage=false 仍记账；重复事件不双扣 | CPU 确定性测试 + GPU；158–193 |
| 幂等与版本价格 | `control_plane/billing.py` | tenant + route + key + 请求体哈希；20 并发最多一次推理；冲突 409；充值只入账一次；调价不改历史 | CPU 竞争测试；172–193、245–264 |
| 用户级额度 | `control_plane/quotas.py` | 多 key 共享余额、token、RPM 和并发；余额只够一次时 20 并发最多一个获准；余额不负；拒绝不进入模型 | CPU 竞争测试；245–264 |
| 持久化和恢复 | `migrations/`、`control_plane/storage.py` | WAL + 事务；已收到最终 usage 立即持久化；无 usage 崩溃保留 unknown + credit_adjustment；有 usage 恰好结算；60 秒内释放预占 | 真实进程 SIGKILL 的两个窗口；272–291 |
| 管理页面 | `console/`、`GET /console` | 真实用户/key/价格/账本/审计操作；实验账本标识；无凭据只返回无敏感信息的登录壳；不把管理员 key 编译进前端 | 浏览器逐项验收；140–156、195–218 |
| Agent | `agent/`、`POST /agent/run` | 输入校验；模型规划→白名单 lookup_order→模型最终回答；父账零费用；每个模型调用一个子账；父子归属和汇总一致；幂等冲突 409 | CPU 流程测试 + GPU 随机订单；876–892、607–619 |
| KV Cache 实验 | `experiments/run.sh --case kv` | 固定 1.5B FP32；on/off × 128/512 × 3，固定 32 decode tokens；同输入/continuation；logits、top1、显存和容量公式 | GPU 原始 12 行 + manifest；453–485 |
| RadixAttention / 前缀复用 | `experiments/run.sh --case prefix` | 7B BF16；on/off × shared/unique × 3 × 8；缓存清除；512 token 前缀；用 cached_tokens 证明命中 | GPU 原始 96 行 + 输入/flush 证据；453–485 |
| FlashAttention | `experiments/run.sh --case attention` | 合成 Q/K/V；math/flash × 128/512/1024 × 3；误差 ≤0.02；profiler 证明实际算子；不静默回退 | GPU 原始 18 行 + 6 个算子文件；453–485 |
| 连续批处理与最终性能 | `configs/baseline.yaml`、`optimized.yaml`、`benchmarks/` | 同负载比较；最终服务保留鉴权和账本；网关不等凑批；中性解码；独立 tokenizer；失败也入分母 | GPU 全矩阵；337–398、559–584 |
| 可追溯交付 | `README.md`、`docs/performance.md`、`results/` | 原理→方法→配置→数据→结论→局限；失败保留；完整 40 位 commit；脱敏原始数据；未执行明确标注 | 本地 + 平台 + 人工；430–437 |

## 容易混淆的边界

- 18 项 CPU 软评测不是正式 15 项凭据检查。软评测没有分数，也没有验证 GPU、模型能力或完整 Console 操作。
- 15 项凭据检查按密钥、计费、配额与恢复各 5 分分组；对应真实 SGLang 部署检查通过后才保留该组得分。
- 最终服务是 7B BF16；KV 数值实验使用固定 1.5B FP32；Attention 使用合成张量。三者不能互换证据。
- SSE 事件不是模型 token；TPOT 代理不是精确逐 token ITL。
- 附件生成区块和配置已经给出确认门槛；后文仍有“待标定/staging”等旧描述。此次核对的线上 acceptance 与附件一致，以其版本化确认信息为性能目标依据；本人的达标状态仍然未知。
- `tools/upstream/` 是公开测试客户端和模型 fixture，不是自己的服务实现。

## 性能目标与本人成果

配置版本：`daytona-4090-v5-confirmed-5e2971c9`。RTX 4090、Qwen/Qwen2.5-7B-Instruct revision `a09a35458c702b33eeacc393d103063234e8bc28`、BF16。

| 目标组 | token/s 最低 | TTFT p95 ms 最高 | TPOT p95 ms/token 最高 | E2E p95 ms 最高 | goodput req/s 最低 | SLO / 成功 / 正确率最低 | 本人实测 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| short C=8 | 71 | 1780 | 35 | 2350 | 3.67 | .95 / .99 / .99 | NOT_TESTED |
| short C=16 | 128 | 1830 | 40 | 2600 | 6.63 | .95 / .99 / .99 | NOT_TESTED |
| long C=8 | 254 | 1870 | 29 | 8900 | .95 | .95 / .99 / .99 | NOT_TESTED |
| long C=16 | 485 | 2070 | 30 | 9350 | 1.81 | .95 / .99 / .99 | NOT_TESTED |

每组必须同时满足全部八项指标；每组 3×16=48 个请求，因此成功率和正确率 ≥99% 实际需要 48/48。baseline/optimized × short/long × C=1/4/8/16 × 3×16，总计 768 个正式测量请求，预热另记。

