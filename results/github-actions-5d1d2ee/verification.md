# 安装重试 CI 核验

固定提交：`5d1d2eea583baec41b047366a2a9e84a6aaf75d3`。两个 workflow 成功，ZIP SHA256 与 GitHub artifact digest 一致。

- CPU 公开检查：18/18，https://github.com/sfdenibao/llm-service/actions/runs/42227539886
- Ubuntu 回归：32/32，0 skip，worktree_dirty=false，https://github.com/sfdenibao/llm-service/actions/runs/42227539790
- 真实 HTTP 断流恢复：pip 25.0.1；第一次 ReadTimeoutError，第二次真实安装、pip check、import 和 marker 全通过，共两次 HTTP 请求。
- 真实 GNU timeout 子树：0.603 秒内 TERM 后 KILL，退出 -9，记录的父子 PID 均 absent。

边界：CPU CI 不是 GPU 冷启动或正式评测。HTTP 实验使用 loopback 小 wheel；尚不能代表外部 CDN 的 706 MB CUDA wheel。Linux 子树实验为合成 sleep 进程树，不实际执行 apt/sudo；sudo 内 timeout 的参数布置由单测另行验证。

原始证据位于同目录 llm-soft-report/ 与 cpu-regression-report/；机器可读核验在 verification.json，来源在 github-metadata.json。
