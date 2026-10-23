# ADR 20261023：显式 host 工具链目录复用原权限链

状态：选定实施，验证与集成待完成。

L1-04 的真实 Go 不可见、反复下载与两次 K1 窄挂载实验表明，需要表达 host 已授权工具链到命令搜索环境的交接。复用现有 EngineeringRunService、PermissionScope、SQLite、K1 sandbox 与物理 process owner，增加可省略的 host-only `toolchain_bin_dirs`，在既有只读挂载内校验后追加到固定系统 PATH。

不从 read_roots 推断工具，不继承 host 全量 PATH/home，不恢复旧执行 owner。缺省/空值保持旧编码和行为；非空在持久化、角色范围、Verifier、恢复与当前 policy 校验中完整迁移。K1 提供共用纯 resolver，Cell 不复制其系统挂载目录表。

代价是一个需跨现有生产者和消费者维护的可选程序字段。它不让模型填 hash/合同，不改变用户目标、授权边界、取消恢复或验收结论。私有临时数据持久化和 QA 场景策略各自独立验证。

完整因果、反例、文件所有权与停止条件见 [设计](../../../../../docs/blueprints/POLARIS_HOST_TOOLCHAIN_BINDING_20261023.md)。实际证据在项目 `.polaris/audits/host-toolchain-mount-20261023/`；此 ADR 本身不证明实现或项目通过。
