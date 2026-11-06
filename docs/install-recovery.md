# 安装超时与下载恢复

2026-11-06 的第一次正式评测使用提交 `aa62145323987fb5e2e182bdc48485bf4f9f0db9`。安装 `nvidia-cudnn-cu12==9.10.2.21` 的706.8 MB wheel时发生 `ReadTimeoutError`，pip退出2、setup退出1，安装阶段耗时61.35秒。它不是耗尽15分钟后的超时；随后缺少PyYAML是安装中断的后果。平台自动评分0/95，GPU推理、机制和性能阶段未执行，人工5分尚无结果。包含连接信息的原始平台报告保存在本机忽略目录，不上传公开仓库。

本次修改保持依赖锁和模型版本不变，不重新提交正式评测：

- pip网络读取超时设为60秒；可识别的网络中断允许最多两轮完整安装尝试，利用pip已有缓存。依赖冲突、构建、认证和完整性错误立即失败。
- 创建环境、依赖安装和服务就绪共享整个setup截止时间，默认840秒，预留30秒清理。可以用 `SETUP_TIMEOUT_SECONDS` 在60–900秒之间设定更短预算。
- pip超时终止该命令创建的进程组；apt安装由root/sudo内部的GNU timeout先发TERM、5秒后再KILL，并给外层留出余量，避免只停sudo而遗留特权子进程。服务启动失败后通过记录PID与创建时间的manifest清理自有服务。
- 每次setup生成独立的 `.runtime/setup-<UTC>-<随机后缀>/`。安装尝试、`pip check`、服务启动及失败清理分别记录，写盘前脱敏。
- 安装及 `pip check` 均成功才原子写入 `.dependencies-sha256`；失败不会留下成功标记。

新增测试使用真正的临时虚拟环境和本机HTTP服务器：第一次只发送128字节后停顿，触发pip读取超时；第二次发送完整wheel，验证可导入、版本、`pip check`、依赖标记和脱敏日志。同时覆盖永久失败不重试、次数上限、检查失败不写标记、共享预算以及超时清理。该测试证明受控断流后的恢复逻辑，不等于外网CDN或GPU冷启动已经通过。

全新GPU验证已通过。2026-11-06 在新的 Daytona RTX 4090 实例拉取固定提交 `5d1d2eea583baec41b047366a2a9e84a6aaf75d3`，预检查确认新目录内 `.venv`、`.venv-gpu`、`.venv-bench`、运行进程manifest和pip缓存均不存在。唯一部署入口为 `bash setup.sh`，没有手动补装或修改云端代码。

| 验证 | 实际结果 |
| --- | --- |
| baseline 从零安装到服务就绪 | 323秒，退出0；本次设定setup上限540秒 |
| 下载与依赖 | cuDNN706.8 MB和Torch899.7 MB均有实际Downloading日志；native/bench的pip check与lock marker通过 |
| baseline / optimized真实HTTP | 各5/5，含鉴权、非流式、SSE；optimized切换32秒 |
| 管理与真实Agent | live 6/6；4类故障注入仍由CPU回归覆盖 |
| 同版本pip24.0断流恢复 | 同机独立临时venv，两次HTTP请求；第一轮超时、第二轮安装及校验成功 |
| 幂等启动 | 1.378秒，退出0，PID与创建时间保持 |
| 项目进程停止 | 21:51:19停止完成，manifest为空、无gateway.pid，GPU快照1 MiB/0% |

整个验证共享650秒截止时间，实际执行及项目清理约380秒。平台提供的模型缓存与wheel目录保留，因此本次证明新实例、新目录、新虚拟环境能重建，不声称全部模型和wheel都从外网下载。主依赖安装第一轮成功；网络故障恢复由同机pip24.0的受控HTTP测试单独证明，未人为中断外网cuDNN下载。没有重跑768条性能矩阵或三项机制实验，也没有提交第二次正式评测。

原始记录、完整验证脚本和进程停止证据在 [cold-start-2026-11-06](../results/cold-start-2026-11-06/)，[独立复核](../reports/cold-start-2026-11-06/review.md) 以各原始JSON/日志为依据。`completion.json` 的 `cloud_machine_still_allocated=true` 保留当时语义；平台实例回收是随后单独检查，见 `platform-stopped.txt/png`。

本机完整回归命令：`.venv\Scripts\python.exe scripts/run_tests.py --out results/regression-2026-11-06-install-retry-02`。32项中31项通过，1项GNU timeout进程树测试因Windows跳过，零失败/错误；该轮Linux项留待Ubuntu CI执行。真实HTTP断流测试两次请求恢复成功，pip25.0.1（本机ensurepip版本），GPU锁仍为pip24.0。报告绑定旧HEAD与 `worktree_dirty=true`，不将未提交工作区结果归给旧提交。之前一轮31/31报告也保留。

固定 `5d1d2ee` 的 [Ubuntu完整回归](https://github.com/sfdenibao/llm-service/actions/runs/42227539790) 为32/32、零跳过、`worktree_dirty=false`；GNU timeout真实合成进程树测试在0.603秒后父子PID均不存在。[公开接口检查](https://github.com/sfdenibao/llm-service/actions/runs/42227539886) 为18/18。两份artifact的完整SHA和ZIP摘要均核对，原字节保存在 [CI归档](../results/github-actions-5d1d2ee/verification.json)。

平台实例于21:54:34另行确认回收，页面显示剩余3分钟练习额度，正式评测历史仍只有 `aa62145` 一次。修复验证没有使用最后一次正式名额。
