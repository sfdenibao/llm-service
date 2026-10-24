# Project 2 · LLM 服务

当前阶段：开工准备。服务代码尚未实现，尚无本项目的 CPU 接口通过结果、GPU 结果或正式成绩。

本项目位于独立的本地 Git 仓库 `homework/llm-service`，初始分支为 `codex/llm-service`。尚未配置远程仓库，也没有提交 SHA。外层术语本与 Project 1-2 的已有修改不属于本项目。

## 从这里开始

1. 阅读 [需求—模块—测试对应表](docs/requirements-matrix.md)，了解每项要求如何验收。
2. 按 [实施顺序](docs/implementation-plan.md) 从第 1 阶段开始。
3. 运行 `python scripts/preflight.py` 查看准备材料、工具哈希与交付文件缺项；这不是接口自测，也不启动服务。

输入资料为 [2026-10-24 任务书副本](docs/taskbook-2026-10-24.md)。性能目标、评分权重和协议均来自该任务书；它们是要求，不是本项目的实测结果。

## 架构

```text
Agent / 客户端
  → 自己的 HTTP 网关：API Key 鉴权、租户身份、排队、SSE
  → 事务账本：额度预占、实际 usage 结算、幂等与故障恢复
  → 引擎适配器
      CPU 阶段：独立确定性 fixture
      GPU 阶段：SGLang + Qwen2.5-7B-Instruct BF16

管理员 → /console → /admin/* → 同一持久化数据库
```

用 Java 项目类比：网关类似 Controller + 鉴权拦截器，排队类似有界线程池的准入，账本类似带数据库事务的业务 Service，SGLang 是负责模型计算的下游服务。Python 的 venv 隔离依赖；本项目的部署合同使用用户态进程，不要求 Docker daemon 或 systemd。

## 当前已准备

- 官方 `.github/workflows/llm-soft.yml`：Ubuntu 24.04 托管 CPU runner，固定 action commit；push 与 workflow_dispatch 触发。
- `tools/upstream/`：按工作流中的固定 SHA-256 验证的公开 CPU runner、HTTP checker 和模型 fixture；详见 [来源记录](tools/upstream/PROVENANCE.md)。
- 附件性能配置快照与当前线上原文件；两份 JSON 解析后的内容一致，线上文件 SHA-256 与任务书声明一致。
- 忽略密钥、运行数据库、模型权重与虚拟环境的 Git 规则。

工作流文件存在不等于 GitHub 已启用或执行。之后需要将本项目绑定到用户自己的 GitHub 仓库并推送；手动触发要求工作流存在于默认分支。此时才会有可关联完整 commit SHA 的 Actions 报告。

## 验证层次

| 层次 | 证明范围 | 当前状态 |
| --- | --- | --- |
| 本地准备检查 | 官方资源哈希、配置一致性、交付文件是否存在 | 见 `results/preflight-2026-10-24.json` |
| 18 项 CPU 软评测 | HTTP 接口、基本鉴权和计费协议 | NOT_TESTED |
| CPU 深入测试 | 事务竞争、SSE 防缓冲、取消、幂等、崩溃恢复 | NOT_TESTED |
| Console 浏览器验收 | 管理页面所有真实交互 | NOT_TESTED |
| 真实 SGLang 联合验收 | GPU 推理、真实 usage、Agent 工具往返 | NOT_TESTED |
| 性能与三项原理实验 | 固定环境的原始数据和配置对照 | NOT_TESTED |
| 正式成绩 / 人工验收 | 平台正式运行 / 本人讲解理解 | NOT_TESTED |

公开 `soft_runner.py` 的入口限定在 GitHub-hosted runner 中运行，本机不伪造 GitHub 环境变量绕过该检查。后续本地自测应使用项目自己的测试编排，或已启动服务上的 HTTP checker，并如实标注执行环境。

