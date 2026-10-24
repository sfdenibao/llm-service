# 公开工具来源

获取日期：2026-10-24。文件作为测试资料原样保存；未执行下载的 runner/checker/fixture。

| 文件 | 来源 | SHA-256 |
| --- | --- | --- |
| llm-soft.yml | https://agentist.org/lab/llm-lab/soft/llm-soft.yml | 6b0a537c5af8b85af76e4ac0622ba0c4649883844997244966e8db97c1ba043d |
| soft_runner.py | https://agentist.org/lab/llm-lab/soft/soft_runner.py | ed9f4f45e2e5cba23bf4c67d58be9ce10e69576e8bdd36904c442788354676cb |
| soft_check.py | https://agentist.org/lab/llm-lab/soft/soft_check.py | 43878383b810358a5dbd8a796c94acd3f6cb39fc8665929a2d5354f079eb65db |
| credential_fixture.py | https://agentist.org/lab/llm-lab/soft/credential_fixture.py | ef860b68237f946d2394f00e48eb2f8d3c17bded84861542232cefbeed66d19c |
| acceptance.live.json | https://agentist.org/lab/llm-lab/acceptance.json | d5cd2fdc90056e710cf741ac1eec4c5b042ea3818d505e4a3975697f1df9241f |

三个 Python 文件的预期哈希取自同次下载的工作流。acceptance 的预期哈希取自用户提供的任务书。`.github/workflows/llm-soft.yml` 与上游文件逐字节一致。

工作流审阅结果：GitHub 托管 Ubuntu 24.04 runner；contents 只读；checkout 不保留凭据；actions 固定 commit；下载工具固定哈希；随机生成临时测试密钥；只运行 CPU contract-test；失败仍上传报告。不需要平台密钥或 GPU 凭据。已存在文件不等于 workflow 已在 GitHub 运行。

附件中的 JSON 提取文件 `docs/acceptance.taskbook.json` 只保证解析后内容一致，不声称它与平台原始文件字节一致。原始线上文件保存在本目录；哈希应针对原始字节计算，避免换行/空白影响。

`soft_runner.py` 明确只允许 GitHub-hosted 环境；不能为了本机执行而伪造环境标识。HTTP checker 可供后续已启动服务的本地诊断使用，但必须清楚记录执行位置和未覆盖项目。

