# 模型重试委派实施计划

方案：[模型服务故障委派](../../blueprints/POLARIS_PROVIDER_RETRY_DELEGATION_20261024.md)。main直接研发，不建分支，目标项目只读。

- [x] 封存R02真实529/44快照、源策略与资源状态；ROOT同原body local200/529/402及实际两层policy基线。
- [x] 先写producer/result/executor/request-resilience真实RED，保留原helper不localretry的intent测试。
- [x] 可选typed类别与SERVICE_UNAVAILABLE request级消费；不改外层/次数/timeout/输出参数或模型路由。
- [x] 真实有效政策metadata与每dispatch审计贯通；兼容旧None/旧编码/旧snapshot只读UNKNOWN。
- [x] final同body与原实际budget对照、耗尽/explicit0/永久错误/取消/期限/circuit、工具效果不重播，相邻与必需门禁/独审。
- [x] main集成冻结新批次，原任务全新隔离验证；旧FAILED不改，不把组件数据升级为真实交付。

当前检查点：三个生产文件完成，ROOT最终静态/架构40/K1 ALL/目录增量和独审通过。27安全、141相邻及原body真实HTTP因果对照通过（各组重叠不累计）。main集成本次源码；全新隔离原生L1-06 R03仍未执行，最后一项只在实际新冻结运行后勾选。证据见方案末节，不能将本地夹具升级为交付。

实际检查点：4ed8c06c集成后，v12 L1-06 R03已完整执行并原生accepted、资源闭合；ROOT质量FAIL两族，严格交付未通过。最后一项只表示新运行已执行，不表示真实质量成功；其余原120仍须串行推进。见v12历史报告。
