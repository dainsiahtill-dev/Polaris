# 持久工程引用先保留对象再发布

状态：accepted design，implementation pending。日期：2026-10-25。

A/B组件实验已复现SQLite事件保留而普通CAS对象可被现有byte GC删除。L2-18历史559缺口的逐文件删除原因仍推断，不能回填成成功。

决定：由现有工程owner通过可信host side channel提供通用引用owner；现有KernelOne同锁保存原字节与V2 pin，随后GC、available与事件。旧V1兼容保留，不恢复Factory合同。查询仍只读，冷owner迁移仅保护存在且身份匹配的字节，缺失与未知保留，不重放。

代价：持久证据可能超过可回收缓存配额，报告protected/unreclaimable与budget_satisfied，不静默删除事实。pin后事件前的保守孤儿不自动回收；明确删除、压缩与分层后续验证。

不新增Cell、工程事实表或控制器，不向模型添加权限/合同字段，不改变24hex读取接口，不做长期记忆。完整设计与停止条件见根目录docs/blueprints/CONTEXT_DURABLE_REFERENCE_RETENTION_20261025.md。
