# LLM Service · Project 2

SGLang 模型服务工程：OpenAI 兼容网关、SSE、用户/API Key、实验计费账本、额度、Agent 工具调用、管理台、可重建部署与 GPU 机制实验。

**已在 Daytona RTX 4090 完成真实 Qwen/SGLang 服务、两配置768次测量与三项机制实验；optimized 384/384成功且正确，四目标共32项公开阈值通过。** baseline为333/384，其51个HTTP504失败原样保留。正式评测尚未提交；最终代码在第二台全新机器的从零重建未验证。[完整实测报告](reports/gpu-2026-11-06-final/summary.md) · [测试记录](docs/test-results.md) · [性能实测](docs/performance.md) · [需求映射](docs/requirements-matrix.md) · [来源](docs/sources.md)

![管理台账本](results/console-browser/ledger.png)

## 当前证据

状态核对日期：2026-11-06（Asia/Shanghai）。历史 CPU 与浏览器结果保留；本次新增真实 GPU、机制与性能证据，并完成固定提交的 Ubuntu CPU 回归。

本次练习机已结束并回收；2026-11-06 20:57（北京时间）页面确认没有运行中的机器，剩余练习额度13分钟。全部实验结果已备份，正式评测尚未提交。[平台回收证据](results/gpu-2026-11-06/platform-stopped.png)

| 层次 | 结果 |
| --- | --- |
| 官方公开 18 项 HTTP checker | 本机 Windows 18/18，`results/cpu-soft-01/soft-report.json` |
| 深入 CPU 回归 | 19/19，`results/regression-03/report.json`；真实 HTTP、事务竞争、信号控制 SSE、取消和强制终止恢复 |
| 2026-11-06 准备阶段回归 | Windows 20/20，`results/regression-2026-11-06-preflight/report.json`；`worktree_dirty=true`，测试对象含尚未提交的修改 |
| HTTP-only 管理自测 | 5 组通过，4 类故障注入明确跳过，另由回归测试覆盖；`results/control-plane-mock-01.json` |
| Console 浏览器 | 用户创建、额度、余额、key 签发/轮换/撤销、停用、价格、账本、审计已操作；`results/console-browser/` |
| GitHub Actions / Ubuntu 24.04 | 提交 `837ca04`：官方公开 checker 18/18、深入回归 24/24；历史 `883276d` 的19/19保留 |
| 真实 SGLang / 管理与 Agent | selftest 5/5、live管理6组通过；实际两次模型调用与工具往返，4类故障注入另由CPU覆盖 |
| 真实服务取消与幂等启动 | 取消恢复7/7，GPU任务释放未证明；同环境 setup 1.329秒、PID保持，不等于冷启动 |
| GPU 机制实验 | KV 12行、prefix 96行、attention 18行通过，见 `results/gpu-2026-11-06/` |
| 完整性能对照 | 各8组384请求；baseline333/384，optimized384/384，optimized四目标32项公开阈值通过 |
| 结束前复检与清理 | 恢复optimized后selftest 5/5；账本预占归零；147文件凭据精确值扫描通过；20:53:08已停止本项目进程，平台实例回收另记 |
| 正式评分 / 人工理解验收 | NOT_TESTED |

2026-10-24 的两份历史 Ubuntu Actions 报告绑定提交 `883276d0f338a83e9f7ab692e0e97b684798d055`：[官方 CPU 软评测](https://github.com/sfdenibao/llm-service/actions/runs/40084779090)（[归档报告](results/github-actions-883276d/soft/report.json)，18 pass、0 missing、0 invalid、0 blocked）；[CPU 回归](https://github.com/sfdenibao/llm-service/actions/runs/40084779009)（[归档报告](results/github-actions-883276d/regression/report.json)，19/19，`execution_environment=linux`）。这些是该提交的 CPU 证据；后续代码提交需关联各自测试结果，不能据此宣称 GPU 或正式评分通过。

2026-11-06 准备阶段修正 Python 3.12 选择与失败证据记录，本机20/20报告的 `worktree_dirty=true` 保留。真实 GPU 部署随后修复 Python.h 缺失、venv ninja PATH 和 SGLang nullable cache usage 解析；首次失败日志与修复后的结果分别归档，不覆盖失败。

本次基准与机制固定提交 `837ca04e52fe800f974cf46f44b7602c87012bb6`，同提交 [CPU软评测18/18](https://github.com/sfdenibao/llm-service/actions/runs/42217691662)、[Ubuntu回归24/24](https://github.com/sfdenibao/llm-service/actions/runs/42217691920) 的报告位于 `results/github-actions-837ca04/`。真实服务基础与管理测试对应 `04d791e30ecbbe4064a7a4d0abf8079fce6be781`，之后的 `837ca04` 增加独立取消探针。GPU 原始数据、环境和各阶段提交标识位于 `results/gpu-2026-11-06/`，数值与边界详见性能文档；公开练习达标不等于官方得分。

同GPU、同seed且对应输入完全相同的对照中，short C8/C16有效吞吐提高5.65/8.48倍，long C8/C16提高7.32/13.62倍。baseline在长输出高并发下有51次约20秒的HTTP504，符合单执行槽的排队超时瓶颈；optimized全部成功。复核报告PASSED表示数据完整、复算一致与optimized阈值通过，保留baseline失败，不产生官方分数。

旧失败记录保留：`regression-01` 中断流清理失败，修复后 `regression-02` 16/16；增加边界后 `regression-03` 19/19。早期报告生成时没有 commit，`commit_sha=null` 保持原样。

## CPU 自测与预览

要求 Python **3.12**。Linux 使用 Bash；Windows 可用 Git Bash。独立环境不要求 Docker/systemd。

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python scripts/check_local.py --out results/my-soft-run
.venv/bin/python scripts/run_tests.py --out results/my-regression-run
.venv/bin/python scripts/dev_demo.py
```

Windows 的解释器路径替换为 `.venv\Scripts\python.exe`。自测创建独立数据库和随机凭据，结束只停自己的进程，保留失败证据；每次使用新结果目录。官方 `soft_runner.py` 只在 GitHub-hosted runner 运行，本地入口使用未修改的 HTTP checker，不伪造 GitHub 环境。

预览只打印本机地址和私有控制文件路径。随机管理员 key 位于忽略的 `.runtime/preview/control.json`，与 DATA_DIR 分离，不提交。创建 `.runtime/preview/stop` 文件可停止预览并清理其控制文件。

## 配置与启动合同

私密值通过环境变量设置，`.env.example` 只有说明，程序不自动加载或输出密钥文件。

| 变量 | 用途 |
| --- | --- |
| API_KEY | ≥32 字符；普通 benchmark key，仍真实计费和限流 |
| ADMIN_API_KEY | 独立管理员 key；缺少时管理 API 关闭，无默认密码 |
| KEY_HASH_SECRET | ≥32 字符 HMAC pepper；同 DATA_DIR 必须一致，缺少/变更时拒绝启动 |
| DATA_DIR | 持久化目录，默认 `.runtime/data`；停止/重跑不删除 |
| HOST / PORT | 默认 `127.0.0.1:8000`；网关端口从环境读取 |
| ENGINE_BASE_URL | 内部 loopback 引擎，默认 `http://127.0.0.1:30001` |
| MODEL_ID / MODEL_REVISION | 固定模型与 revision，见下文 |
| BENCH_PROFILE | baseline / optimized |
| HF_HOME | 模型下载缓存，由依赖库读取 |

外部 CPU fixture 已启动时：

```bash
bash setup.sh --contract-test
bash setup.sh --contract-test
bash setup.sh --stop
```

contract-test 只替换模型引擎，鉴权、账本、配额、恢复代码与 live 相同，不安装 SGLang/CUDA/权重。`gateway.pid` 对应实际监听进程；`processes.json` 记录 PID 与创建时间，防止误杀复用 PID。配置/代码/私密配置摘要变化会重建自己的进程；同配置幂等保持 PID。

Linux RTX 4090：`BENCH_PROFILE=baseline bash setup.sh`，之后 `BENCH_PROFILE=optimized bash setup.sh`。live 使用 `.venv-gpu` 完整版本锁和独立 `.venv-bench` 客户端锁。SGLang 0.5.9 / PyTorch 2.9.1 / Transformers 4.57.1 已在本次练习机运行；setup 会核查原生编译器和 Python 开发头文件，并将 venv 工具加入 PATH。本次经历现场修复与已有缓存重跑，最终代码在第二台全新机器从零重建仍待验证。

## 请求与账本

```text
客户端 → Key 鉴权 → 用户额度 / 事务预占 → FIFO 有界排队
      → SGLang（同 request_id）→ JSON / 实时 SSE → usage 落盘 → 恰好一次结算
管理员 → /console → /admin/* → 同一 SQLite WAL 数据库
```

- `/healthz` 公共，依赖实际引擎就绪；其他模型/用量/指标接口需要 Bearer key。CPU `/info` 明确报告 cpu-fixture，不宣称 GPU 已部署。
- 支持 model/messages/temperature/max_tokens/stream/stream_options.include_usage/response_format；未知字段拒绝；JSON 模式原样转发，不修补答案。
- 用户和 key 由服务端鉴权确定。输入包含固定 chat template；CPU count 来自 fixture。客户端不能指定账单 usage、费用或角色。
- 整数 micro-USD；初始输入 2、输出 6，100+50 tokens 费用 500。余额检查和预占同一事务完成，多 key 共享用户总限额。
- 网关始终取得真实 usage，即使客户端不请求。最终 usage 立即 journal；正常 DONE 前结算。缺少 finish/usage/DONE 输出 error，不伪造成功。
- 断流/超时发送同 rid abort，关闭上游并释放槽位；AnyIO shield 保护清理不被重复取消。
- 无可信用量的中断先 pending_reconciliation；恢复 abort 后，以审计 credit_adjustment 释放预占，input/output/cost 保持 null，标 unknown_released。已有 journal 的用量恰好结算一次。
- Idempotency-Key 按 tenant+route+body hash 去重，进行中/已结束及 body 冲突返回 409。Agent 父账金额为 0，规划和最终回答分别形成模型调用子账。
- 原子零宽限轮换；已获准请求可按旧 key 完成，其后新请求 401。撤销/到期 401，停用 403，余额/token 不足 402，RPM/并发/队列满 429 + Retry-After。
- HTTP access log 关闭，校验错误不回显原输入。Console 的管理员凭据只在页面内存，不写 storage/cookie；所有管理写操作使用 Bearer header。

metrics 的 active/queued 为当前状态，累计计数在网关重启时清零，账本不清零。live 显存由 nvidia-smi 观察自有进程；不可观察时为 null。abort ACK 本身不证明 GPU 计算已经停止。

## 模块映射

| 模块 | 文件 |
| --- | --- |
| HTTP / 输入 / 管理 API | `server/app.py`、`models.py` |
| 引擎 / SSE / 生命周期 | `server/engine.py`、`streaming.py`、`service.py` |
| 准入 / 指标 | `server/admission.py`、`metrics.py` |
| Key / 配额 / 价格 / 账本 / 恢复 | `control_plane/storage.py`，共享单一事务边界 |
| 数据结构 | `migrations/001_initial.sql`，幂等执行 |
| 部署 / 进程 | `setup.sh`、`server/bootstrap.py`、`supervisor.py`、`main.py` |
| Agent / 管理台 | `agent/runner.py` / `console/` |
| 测试 / 故障控制 | `scripts/check_control_plane.py`、`tests/` |

## GPU 实验与压测

```bash
bash experiments/run.sh --case kv --out results/gpu-run-01/kv
bash experiments/run.sh --case prefix --out results/gpu-run-01/prefix
bash experiments/run.sh --case attention --out results/gpu-run-01/attention
BENCH_PROFILE=optimized bash setup.sh
```

实验会先停止本项目服务释放显存，之后可用 setup 恢复。KV 为固定 1.5B FP32 revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`；最终服务与 prefix 为 7B BF16 revision `a09a35458c702b33eeacc393d103063234e8bc28`；attention 为合成张量。

```bash
.venv-gpu/bin/python scripts/download_tools.py --out tools/public-fresh-run
export LLM_LAB_TOOLS="$PWD/tools/public-fresh-run"
BENCH_PROFILE=baseline bash setup.sh
BENCH_PROFILE=baseline bash benchmarks/run.sh
BENCH_PROFILE=optimized bash setup.sh
BENCH_PROFILE=optimized bash benchmarks/run.sh
```

下载器校验完整九文件与 manifest，不自动推理。压测保持鉴权、额度、账本，使用相同 seed，原始记录和汇总写入 `results/serving`；后续新实验用公开 CLI 指定新的 `--out`。

```bash
python scripts/check_control_plane.py --base-url http://127.0.0.1:8000 --suite mock --out results/control-mock-new.json
python scripts/check_control_plane.py --base-url http://127.0.0.1:8000 --suite live --out results/control-live-new.json
```

HTTP-only 自测不申请 GPU，并明确列出无法注入的故障项；用 `scripts/run_tests.py` 验证这些项。live 另验证真实 Agent 规划与工具往返。push 只触发 CPU Actions；正式 GPU 评测需对已推送的完整 40 位 SHA 主动提交。所有自测不生成官方分数。

## 排障

healthz 503 时查看 DATA_DIR 内 engine/gateway 日志和模型身份。端口冲突时核对占用者，脚本不按端口杀无关进程。pepper 不一致时恢复原 KEY_HASH_SECRET，不删除数据库绕过。未知用量查 usage_state 和审计，释放预占不代表得到精确用量。GPU 安装/数值失败保留日志与原始数据，不改容差或复制旧结果。

运行数据库、私密环境、权重和 venv 均被 Git 忽略。公开仓库只放源代码、固定依赖、公开工具和脱敏证据。
