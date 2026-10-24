# 测试记录

2026-10-24，Windows / Python3.12.10，独立venv与每次新建合成数据库。

| 证据（results目录下） | 结果 | 边界 |
| --- | --- | --- |
| preflight-2026-10-24.json | 7项资料校验 | 保留当时未实现状态 |
| cpu-soft-01/soft-report.json | 官方公开checker 18/18 | 本机CPU，非Actions/GPU |
| regression-01/report.json | 15/16，取消清理失败 | 失败证据原样保留 |
| regression-02/report.json | 修复后16/16 | AnyIO取消作用域屏蔽 |
| regression-03/report.json | 19/19 | 加入排队断开、在途撤销/价格冻结、非法SSE |
| control-plane-mock-01.json | 5组通过，4类跳过 | 故障注入项由独立回归覆盖 |
| console-browser/ | 页面操作和截图 | 合成账本，非GPU及用户验收 |

第一阶段：就绪取决于真实下游；引擎停机503、恢复200；同配置重跑PID不变；stop保留数据库及外部fixture。第二阶段：事务预占防止透支；20并发余额竞争只放行1笔、其余402；相同幂等键20次只生成/扣费1次，其余409；多key共享用户额度。

第三阶段：信号控制证明边读边发；断流发送abort并清理；无usage的崩溃恢复保留null并审计释放；已有journal的恢复精确扣一次。第四阶段：管理页连接实际API；Agent父账为0、两次模型调用记子账。CPU Agent fixture只证明流程与关联，不证明真实模型能力。

早期报告没有commit，原null不回填；后续提交运行应关联完整SHA。GPU部署、三项机制矩阵、模型质量、性能SLO、正式95分及人工5分仍NOT_TESTED。
