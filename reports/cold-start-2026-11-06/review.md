# 冷启动独立证据复核

结论：**PASSED（练习验证，不是正式分数）**。固定代码 `5d1d2eea583baec41b047366a2a9e84a6aaf75d3`，RTX 4090、Python 3.12.3、pip 24.0。

本报告只读复核 `results/cold-start-2026-11-06/` 的 42 份文件，逐一保存 SHA256。源目录未作修改。开始于 2026-11-06T13:44:58.940237+00:00，服务停止于 2026-11-06T13:51:19.095179+00:00，整段验证 380.155 秒。

| 核验项 | 结果 | 原始证据 |
|---|---|---|
| 新环境边界 | 5 个项目路径均不存在，提交与 4 份 lock 哈希匹配 | preflight.json |
| 缺失 Python headers | sudo 内 GNU timeout 保护的 apt update/install 成功；两个 venv 真实新建 | logs/setup-20261106T134459Z-6a7b5057__prerequisites__apt-*.log / create-*-venv.log |
| baseline 从零安装与就绪 | 323 秒，退出 0 | logs/cold-baseline-timing.json |
| optimized 切换 | 32 秒，复用本次依赖环境，退出 0 | logs/cold-optimized-timing.json |
| 两 profile 真实请求 | selftest 各 5/5，含流式与非流式 | baseline-selftest.json / optimized-selftest.json |
| 真实凭据业务 | 6/6 通过，另 4 类故障测试跳过 | control-live.json |
| 同机断流恢复 | pip 24.0；两次 HTTP 请求，首次读取超时，第二次安装、pip check、import、marker 均通过 | installer-test/bootstrap-install-recovery/ |
| 依赖一致性 | 两环境 pip check 成功；记录显示 marker 匹配 lock | dependency-checks.json / 原始 pip-check.log |
| optimized 实参 | flashinfer、32 并发、16384 tokens、radix cache 开启 | runtime-audit.json |
| 幂等启动 | 1.378 秒，退出 0，自有进程保持 | idempotency.json |
| 服务清理 | run/stop 均 0，自有进程为空、gateway PID 不存在，GPU 1 MiB / 0% | completion.json |
| 导出脱敏 | 原始凭据扫描记录通过，覆盖 39 份文件 | credential-scan.json |

安装日志第 **434** 行是完整 cuDNN 9.10.2.21 wheel **706.8 MB** 的 `Downloading`，第 **505** 行是完整 torch 2.9.1 wheel **899.7 MB** 的 `Downloading`，对应安装退出 0；这两个 wheel 没有 `Using cached` 或本地 wheel `Processing` 记录。具体行文及来源 SHA256 见 review.json。

## 结论边界

- 这是独立的只读证据复核，没有重新操作云机、执行安装或提交正式评测。
- 冷启动范围是新项目目录、新虚拟环境和新项目 pip 下载缓存；平台提供的 Hugging Face 模型缓存及 /opt/llm-cache/wheels 保留。
- cuDNN 与 torch 的完整 wheel 在本次 pip 日志中明确为 Downloading，不能据此推断所有模型或依赖均从外网下载。
- 大 wheel 本次第一次安装成功；超时重试能力由同机 pip 24.0 的独立 loopback HTTP 小 wheel 断流实验验证，没有主动对真实 CUDA wheel 或外部 CDN 注入故障。
- optimized 的 32 秒是复用本次新建环境后的 profile 切换，不是第二次从零安装。
- 两个 selftest 只验证真实服务基本请求；optimized 流式样例 TTFT 约 6.65 秒，不是性能矩阵或 SLO 结论。
- live control 有 4 类故障测试跳过；本报告不把它们计为通过。
- marker 匹配和幂等 PID 保持依据云端生成的 dependency-checks/idempotency 记录；marker/PID 原文件未作为独立原件导出。
- completion 仅证明本项目服务已停止；该采样时云机仍 allocated。平台回收需要另行 UI 证据。
- 未执行第二次正式评测，也不据本结果改写第一次正式评测的 0/95。
