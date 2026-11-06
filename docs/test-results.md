# 测试记录

状态核对日期：2026-11-06（Asia/Shanghai）。历史测试执行于 2026-10-24；另新增 2026-11-06 本机准备阶段回归。不改写旧报告或执行日期。本机使用 Windows / Python3.12.10、独立venv与每次新建合成数据库；历史 GitHub Actions 使用 Ubuntu 24.04 / Python3.12。

| 证据（results目录下） | 结果 | 边界 |
| --- | --- | --- |
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

两份 Actions 报告均绑定完整提交 `883276d0f338a83e9f7ab692e0e97b684798d055`。运行入口：[官方 CPU 软评测 #40084779090](https://github.com/sfdenibao/llm-service/actions/runs/40084779090)、[深入 CPU 回归 #40084779009](https://github.com/sfdenibao/llm-service/actions/runs/40084779009)。回归报告记录的执行时间为 2026-10-24 18:09:30–18:09:53（Asia/Shanghai）。归档的原始报告、setup 日志与 unittest 日志保持原样。

历史回归报告的 `not_tested` 中保留了旧生成器固定写入的 `GitHub Actions` 和 `Linux SIGKILL when running on Windows` 标签；这些静态标签当时没有按运行环境更新。该次运行的平台应结合上述 Actions 记录、`execution_environment=linux` 与日志判断，不能把标签当成它在 Windows 上执行的证据，也不据此扩展到 GPU 验证。2026-11-06 已修正生成器，旧报告原样保留。

2026-11-06 本机回归于 20:07:46–20:08:20（Asia/Shanghai）完成 20/20：保留原 19 项 HTTP 集成测试，新增实验失败证据测试，验证失败时 `complete=false`、输出 traceback 文件并脱敏凭据。启动入口同时改为精确选择 Python 3.12。命令：`.venv\Scripts\python.exe scripts/run_tests.py --out results/regression-2026-11-06-preflight`。报告记录 HEAD `883276d0f338a83e9f7ab692e0e97b684798d055` 与 `worktree_dirty=true`，因此验证对象是带未提交修改的工作区，不能将 20 项结果归给旧提交；提交后需以新 SHA 的对应报告为准。

第一阶段：就绪取决于真实下游；引擎停机503、恢复200；同配置重跑PID不变；stop保留数据库及外部fixture。第二阶段：事务预占防止透支；20并发余额竞争只放行1笔、其余402；相同幂等键20次只生成/扣费1次，其余409；多key共享用户额度。

第三阶段：信号控制证明边读边发；断流发送abort并清理；无usage的崩溃恢复保留null并审计释放；已有journal的恢复精确扣一次。第四阶段：管理页连接实际API；Agent父账为0、两次模型调用记子账。CPU Agent fixture只证明流程与关联，不证明真实模型能力。

早期报告没有commit，原null不回填；后续提交运行应关联完整SHA。GPU部署、三项机制矩阵、模型质量、性能SLO、正式95分及人工5分仍NOT_TESTED。
