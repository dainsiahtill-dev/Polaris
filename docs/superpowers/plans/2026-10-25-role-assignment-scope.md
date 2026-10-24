# 当前角色任务说明实施计划

Spec: `docs/blueprints/POLARIS_ROLE_ASSIGNMENT_SCOPE_20261025.md`。

全局约束：main直接研发；不创建分支/worktree；目标项目只读；保留失败和未知；KFS/KernelOne复用；同一EngineeringRunService/SQLite；不增权限、状态owner、合同或重试规则。

1. 核对真实实验的原始请求、角色终态、预算、权限与B规划的单位矛盾；区分规划结束、规划质量与项目交付。
2. 生产实现桶为`engineering_roles.py`、`public/engineering_service.py`、`engineering_loop.py`及`tests/test_engineering_roles.py`。生产者增加当前角色任务，实际协作消费者传角色；默认/Director行为保持兼容，原目标和已有建议完整保留。相邻空工具生产者只在schema非空时发送auto，空权限不扩大，provider验证器不放宽。另一个互斥测试配置桶为`tests/engineering_support.py`，显式配置真实K1重试owner以匹配有限脚本；不修改生产重试代码或放宽原断言。
3. 先构造真实入口/请求RED，再修生产者与消费者；覆盖PM/CE不同职责、相邻咨询、权限交集、原始目标优先和冷恢复。替身范围明确。
4. 运行相关角色/上下文/恢复/执行回归及必要KernelOne/架构/目录检查；独立审阅精确候选；不以测试数量当交付。
5. 集成并冻结新版本，使用原L2-14和已选Rust profile重新隔离运行。真实QA、个人质量与自动闭合完成后再推进下一原目录项目；保留v21 CANCELLED。

当前状态：步骤1已独立核证，仅支持单次新鲜配对中的完成信号；B规划的金额矛盾与晚到审计记录边界保留。设计已审阅批准，步骤2—3的职责说明、空工具迁移已完成组件检查，测试配置桥在独立文件桶中收尾。最终集成和新冻结项目验收待完成。文档与报告由ROOT维护；实现者不得越桶修改，冻结期间所有源码写入者停止。
