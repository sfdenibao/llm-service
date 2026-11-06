# 安装超时与下载恢复

2026-11-06 的第一次正式评测使用提交 `aa62145323987fb5e2e182bdc48485bf4f9f0db9`。安装 `nvidia-cudnn-cu12==9.10.2.21` 的706.8 MB wheel时发生 `ReadTimeoutError`，pip退出2、setup退出1，安装阶段耗时61.35秒。它不是耗尽15分钟后的超时；随后缺少PyYAML是安装中断的后果。平台自动评分0/95，GPU推理、机制和性能阶段未执行，人工5分尚无结果。包含连接信息的原始平台报告保存在本机忽略目录，不上传公开仓库。

本次修改保持依赖锁和模型版本不变，不重新提交正式评测：

- pip网络读取超时设为60秒；可识别的网络中断允许最多两轮完整安装尝试，利用pip已有缓存。依赖冲突、构建、认证和完整性错误立即失败。
- 创建环境、依赖安装和服务就绪共享整个setup截止时间，默认840秒，预留30秒清理。可以用 `SETUP_TIMEOUT_SECONDS` 在60–900秒之间设定更短预算。
- pip超时终止该命令创建的进程组；apt安装由root/sudo内部的GNU timeout先发TERM、5秒后再KILL，并给外层留出余量，避免只停sudo而遗留特权子进程。服务启动失败后通过记录PID与创建时间的manifest清理自有服务。
- 每次setup生成独立的 `.runtime/setup-<UTC>-<随机后缀>/`。安装尝试、`pip check`、服务启动及失败清理分别记录，写盘前脱敏。
- 安装及 `pip check` 均成功才原子写入 `.dependencies-sha256`；失败不会留下成功标记。

新增测试使用真正的临时虚拟环境和本机HTTP服务器：第一次只发送128字节后停顿，触发pip读取超时；第二次发送完整wheel，验证可导入、版本、`pip check`、依赖标记和脱敏日志。同时覆盖永久失败不重试、次数上限、检查失败不写标记、共享预算以及超时清理。该测试证明受控断流后的恢复逻辑，不等于外网CDN或GPU冷启动已经通过。

全新GPU验证计划使用独立新实例、固定提交的新目录、全新 `.venv-gpu` 和 `.venv-bench` 以及空pip下载缓存；保留平台提供的模型缓存和wheel目录。依次检查baseline启动、真实HTTP自测、optimized切换、管理与Agent流程、依赖标记和幂等。实际执行前状态为 **NOT_TESTED**；结果将另行记录，不以旧环境的1.329秒幂等启动代替。

本机完整回归命令：`.venv\Scripts\python.exe scripts/run_tests.py --out results/regression-2026-11-06-install-retry-02`。32项中31项通过，1项GNU timeout进程树测试因Windows跳过，零失败/错误；Linux项等待Ubuntu CI。真实HTTP断流测试两次请求恢复成功，pip25.0.1（本机ensurepip版本），GPU锁仍为pip24.0。报告绑定旧HEAD与 `worktree_dirty=true`，不将未提交工作区结果归给旧提交。之前一轮31/31报告也保留。
