# 测试记录

状态核对日期：2026-11-06（Asia/Shanghai）。历史测试执行于 2026-10-24；本次新增 Windows准备回归、Ubuntu Actions、Daytona RTX4090真实服务与实验结果。不改写旧报告或执行日期。本机使用 Windows / Python3.12.10；Actions 使用 Ubuntu 24.04 / Python3.12；GPU 为 Daytona Linux / Python3.12.3。

正式流程补查：本机执行 `.venv\Scripts\python.exe scripts/run_tests.py --out results/regression-2026-11-06-formal-flow`，51项中46项通过、5项因Linux专属信号行为在Windows跳过，零失败/错误。报告的旧HEAD与 `worktree_dirty=true` 如实保留；该结果不属于旧提交的原始代码。新增隔离checkout的实际调用顺序为 CPU凭据服务→无DATA_DIR停止→另一DATA_DIR的baseline→三项最小环境实验入口→optimized→幂等→重复停止，六阶段全部通过，cleanup_errors为空。HTTP服务与停止均为真实进程；实验入口只替换CUDA探针和数值函数，明确不证明GPU推理或评分。此前旧实现的同类停止测试失败，私有复现记录保留在 `.runtime/formal-flow-fix-before/`。

独立实验配置、缓存继承/显式优先、失败证据与SIGTERM处理、registry所有权/多目录/锁竞争均有聚焦用例。另增加四项真正的Linux进程测试，覆盖父提前退出与不同进程组、注册期间TERM、注册失败清理、连续TERM和拒绝TERM的子进程；这些在本机明确跳过，必须以新提交对应Ubuntu Actions结果为准。本轮未启动GPU、未提交正式评测。

20:53停止项目服务并导出全部结果后，经用户确认回收云端练习机。20:57平台页面确认当前没有运行中的机器，显示剩余13分钟；见 [回收截图](../results/gpu-2026-11-06/platform-stopped.png) 与 [状态记录](../results/gpu-2026-11-06/platform-state.json)。这证明平台已回收实例，与前面的HTTP取消是否立即释放GPU任务是不同检查。

| 证据（results目录下） | 结果 | 边界 |
| --- | --- | --- |
| github-actions-5d1d2ee/ | 公开checker18/18；Ubuntu32/32、零跳过 | 固定完整SHA、ZIP摘要核验；CPU而非正式分数 |
| cold-start-2026-11-06/ | baseline冷启动323秒；两profile各5/5、管理6/6、同机pip24断流恢复 | 新实例和虚拟环境，平台模型与wheel缓存保留；未重跑性能矩阵或正式评测 |
| cold-start-2026-11-06/platform-state.json | 21:54:34云端无运行机器，剩余3分钟 | 项目进程此前21:51:19已停；平台回收单独核验 |
| preflight-2026-10-24.json | 7项资料校验 | 保留当时未实现状态 |
| cpu-soft-01/soft-report.json | 官方公开checker 18/18 | 本机CPU，非Actions/GPU |
| regression-01/report.json | 15/16，取消清理失败 | 失败证据原样保留 |
| regression-02/report.json | 修复后16/16 | AnyIO取消作用域屏蔽 |
| regression-03/report.json | 19/19 | 加入排队断开、在途撤销/价格冻结、非法SSE |
| control-plane-mock-01.json | 5组通过，4类跳过 | 故障注入项由独立回归覆盖 |
| console-browser/ | 页面操作和截图 | 合成账本，非GPU及用户验收 |
| github-actions-883276d/soft/report.json | 官方公开 checker 18/18；0 missing、0 invalid、0 blocked | Ubuntu Actions CPU；`gpu_used=false`、`official_score=false` |
| github-actions-883276d/regression/report.json | 19/19，`execution_environment=linux`；unittest 日志为 OK | Ubuntu Actions CPU；`official_score=false` |
| regression-2026-11-06-preflight/report.json | 20/20，`execution_environment=win32` | 含未提交修改，`worktree_dirty=true`；CPU，非新提交的 Actions 或 GPU 证据 |
| github-actions-837ca04/soft/report.json | 官方公开checker 18/18 | 固定 `837ca04` 的Ubuntu CPU，非正式评分 |
| github-actions-837ca04/regression/report.json | 24/24，linux，`worktree_dirty=false` | 新增失败证据、原生构建前置检查与nullable usage回归 |
| gpu-2026-11-06/setup-attempt-01、02/ | Python.h / ninja PATH失败证据保留 | 后续修复；不是最终代码在第二台机器的从零重建 |
| gpu-2026-11-06/selftest-02.json | health/models/auth/nonstream/stream 5/5 | 真实7B模型；短OK任务不替代最终结构化性能负载 |
| gpu-2026-11-06/control-live.json | 6组通过，含真实Agent两次模型调用与工具结果一致 | 4类故障注入明确跳过，仍由CPU回归覆盖 |
| gpu-2026-11-06/idempotent-setup.json | 1.3287秒，退出0，PID保持 | 同环境幂等；不是冷启动 |
| gpu-2026-11-06/live-cancel-01.json | 7/7，断流后账本与配额收敛，后续请求成功 | `gpu_task_release_proven=false`，不证明GPU内部任务/显存释放 |
| gpu-2026-11-06/kv/ | 12行，数值等价通过 | 独立1.5B FP32，非7B服务吞吐 |
| gpu-2026-11-06/prefix/ | 96行，cached_tokens及输出通过 | 原生引擎机制实验，非最终服务SLO |
| gpu-2026-11-06/attention/ | 18行，数值与后端通过 | 合成QKV与PyTorch算子，非SGLang完整算子追踪 |
| gpu-2026-11-06/serving/optimized-serving-summary.json | 8组384/384成功且正确；四目标32阈值通过 | 公开练习负载，非正式分数 |
| gpu-2026-11-06/serving/baseline-serving-summary.json | 8组384请求，333成功且正确，51个HTTP504 | long C8成功30/48、C16成功15/48，其余6组48/48；失败保留分母 |
| ../reports/gpu-2026-11-06-final/summary.json | PASSED，16格768条完整且复算一致，optimized32项阈值通过 | `official_score=false`；不表示baseline全部成功 |
| gpu-2026-11-06/optimized-runtime-audit.json、selftest-final.json | 恢复optimized后，实际引擎参数与原生metadata一致，基础自测5/5 | 结束前复检，非新全矩阵或冷启动 |
| gpu-2026-11-06/benchmark-billing-summary.json | reserved金额与tokens均为0 | 累计873条含预热/自测，unknown_usage_count=2；不把未知用量当零费用 |
| gpu-2026-11-06/credential-scan.json | 147个导出文件对三项运行凭据精确值扫描通过 | 扫描范围按报告，不代表穷尽所有敏感信息格式 |
| gpu-2026-11-06/service-stop.json | 20:53:08停止自有进程，清单为空、PID文件无，GPU快照1 MiB/0% | 整个服务已停；当时平台实例仍allocated，非云端回收证明 |

本次基准与三项机制实验固定 `837ca04e52fe800f974cf46f44b7602c87012bb6`，对应 [CPU软评测18/18](https://github.com/sfdenibao/llm-service/actions/runs/42217691662)、[Ubuntu回归24/24](https://github.com/sfdenibao/llm-service/actions/runs/42217691920)，归档摘要与ZIP哈希校验见 `github-actions-837ca04/archive-verification.json`。真实基础/管理测试由 `live-tested-commit.txt` 标识为 `04d791e30ecbbe4064a7a4d0abf8079fce6be781`；之后 `837ca04` 增加独立HTTP取消探针，服务代码不变。环境、模型revision、每项manifest与输入均保留。

真实部署先后暴露编译扩展所需 Python.h 缺失、已安装的 ninja 未进入子进程 PATH，以及 SGLang cache usage 细项可为 null 导致解析失败。补齐构建前置检查、传递 venv PATH、接受 nullable 细项后通过上述测试，修复已进入源码提交。`selftest.json` 与首次部署日志保留原失败，修复结果另存 `selftest-02.json`。上述首次练习中的已有依赖重跑不证明冷启动；后续固定 `5d1d2ee` 已在新实例、新目录、新虚拟环境完成323秒启动，原始记录见 `cold-start-2026-11-06/`，平台模型与wheel缓存边界保留。

两份 Actions 报告均绑定完整提交 `883276d0f338a83e9f7ab692e0e97b684798d055`。运行入口：[官方 CPU 软评测 #40084779090](https://github.com/sfdenibao/llm-service/actions/runs/40084779090)、[深入 CPU 回归 #40084779009](https://github.com/sfdenibao/llm-service/actions/runs/40084779009)。回归报告记录的执行时间为 2026-10-24 18:09:30–18:09:53（Asia/Shanghai）。归档的原始报告、setup 日志与 unittest 日志保持原样。

历史回归报告的 `not_tested` 中保留了旧生成器固定写入的 `GitHub Actions` 和 `Linux SIGKILL when running on Windows` 标签；这些静态标签当时没有按运行环境更新。该次运行的平台应结合上述 Actions 记录、`execution_environment=linux` 与日志判断，不能把标签当成它在 Windows 上执行的证据，也不据此扩展到 GPU 验证。2026-11-06 已修正生成器，旧报告原样保留。

2026-11-06 本机回归于 20:07:46–20:08:20（Asia/Shanghai）完成 20/20：保留原 19 项 HTTP 集成测试，新增实验失败证据测试，验证失败时 `complete=false`、输出 traceback 文件并脱敏凭据。启动入口同时改为精确选择 Python 3.12。命令：`.venv\Scripts\python.exe scripts/run_tests.py --out results/regression-2026-11-06-preflight`。报告记录 HEAD `883276d0f338a83e9f7ab692e0e97b684798d055` 与 `worktree_dirty=true`，因此验证对象是带未提交修改的工作区，不能将 20 项结果归给旧提交；提交后需以新 SHA 的对应报告为准。

第一阶段：就绪取决于真实下游；引擎停机503、恢复200；同配置重跑PID不变；stop保留数据库及外部fixture。第二阶段：事务预占防止透支；20并发余额竞争只放行1笔、其余402；相同幂等键20次只生成/扣费1次，其余409；多key共享用户额度。

第三阶段：信号控制证明边读边发；断流发送abort并清理；无usage的崩溃恢复保留null并审计释放；已有journal的恢复精确扣一次。第四阶段：管理页连接实际API；Agent父账为0、两次模型调用记子账。CPU Agent fixture只证明流程与关联，不证明真实模型能力。

baseline与optimized对应24份输入文件逐字节一致，seed均为1093552657。baseline的51次失败在raw中均为HTTP504、content_events=0、TTFT=null、E2E为20.033–20.168秒；metrics的timed_out=51。结合单槽与20秒排队上限，符合排队超时瓶颈；raw未保存504响应体，未将其解释为模型答错或显存不足。复核保留所有失败在分母中，细节见 [完整实测报告](../reports/gpu-2026-11-06-final/summary.md)。

早期报告没有commit，原null不回填；后续提交运行应关联完整SHA。本次真实GPU、三项机制与两配置完整对照已有上表证据。首次正式评测在 cuDNN 下载超时后终止，自动0/95，GPU阶段未执行，人工5分尚未验收；全新环境重建已通过，GPU内部单请求任务/显存释放仍未证明。安装修复与新增测试见[记录](install-recovery.md)。具体性能数值与实验解释见 [性能实测](performance.md)。
