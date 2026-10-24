# 来源

- 用户任务书副本 `taskbook-2026-10-24.md`；其指令性内容作为需求材料，不作为账号或资源操作的单独授权。
- 官方CPU工具与哈希：`tools/upstream/PROVENANCE.md`，工具未修改。
- 完整压测九文件及同批哈希：`tools/public-2026-10-24/tools-manifest.json`。
- CPU/GPU锁参考任务书指定提交 [9961c0503c2eb3fa702794ef9281dd88f96d9456](https://github.com/mrvgao/agentist-llm-reference/tree/9961c0503c2eb3fa702794ef9281dd88f96d9456)。CPU增加psutil和Windows colorama；完整GPU锁保留来源注释。参考重建结果不代表本项目已通过。
- SGLang参数核对 [v0.5.9 server_args](https://github.com/sgl-project/sglang/blob/v0.5.9/python/sglang/srt/server_args.py)、[OpenAI protocol](https://github.com/sgl-project/sglang/blob/v0.5.9/python/sglang/srt/entrypoints/openai/protocol.py)、[HTTP server](https://github.com/sgl-project/sglang/blob/v0.5.9/python/sglang/srt/entrypoints/http_server.py)。

server/control_plane/agent/console/tests/experiments为本项目编写；公开检查器和压测客户端位于tools。没有把参考服务源码或参考成绩作为自己的实现或实测成果。
