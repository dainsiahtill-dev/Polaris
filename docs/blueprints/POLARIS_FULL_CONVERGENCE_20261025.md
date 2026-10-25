# Polaris 全仓技术债务与架构收敛

状态：用户已明确授权执行；当前基线main d5217cdfc；新Native启动暂缓。长期记忆排除。所有源码直接main，无开发分支/worktree。

## 目标与完成口径

顺序固定：已知技术债务清零，完成全仓架构和接口收敛，再回归/独立审查，冻结版本，串行执行原目录L1–L12全部120项并逐项Root亲验。源码资格、真实模型运行、QA、Root质量、资源结算分别证明。

现有catalog159处/115独立指纹不再用旧baseline签发通过。每一项以根因、修复、原反例、相邻生产者/消费者、持久化/恢复和结果证据关闭；不能靠删测试/删功能、扩大allowlist/依赖/权限或改计数凑零。历史FAILED/CANCELLED/UNKNOWN保留，不是待删除的代码债务。

## 已建立的基线

- FactStream公开面25项：根facade、已有DTO导出、四份有序投影及过时能力清单；真实payload能力有生产者/恢复读取者，保留。
- 跨Cell私有导入60项/103处/54文件：3个非测试位置，其余在测试。包含一个被同名后继package遮蔽的旧文件，必须证明实际解析和动态使用后才退役。
- 缺依赖23项：4条生产public依赖、19条测试依赖；不能把测试依赖全加进生产graph。
- 未声明effect6项/7处：均在测试；不能给生产Cell普遍授予subprocess。
- 新增环1项只是差异规则的结果；须另外枚举全部真实SCC和旧allowlist覆盖的环，不以差异清零冒充DAG收敛。

原始逐项证据在主仓`.polaris/audits/technical-debt-clearance-20261025/`，不成为产品事实源。

## 设计不变量

保留Cell与KernelOne，现有状态owner优先。工程写入/取消/恢复只由EngineeringRunService；工程SQLite唯一，FactStream与K1技术持久化各按现有职责。角色是同一工程循环中的专业能力，不恢复模型hash/OBL/合同维护协议或旧Factory写准入。

公开facade、合同、调用方、catalog/manifest/generated投影必须表达同一真实能力。静态与字符串/dynamic调用一并核查；真实功能按当前、显式可选、历史只读、退役四态记录，任何退役都需调用证据和替代/回归。

测试也有真实边界：同owner私有单测保留；跨owner集成使用真实public authority。必要时把错误归属的单测迁回真实owner，并保留public交接集成验证。引入独立、受限测试依赖/effect声明来修正生产/测试混淆，checker继续拒绝未声明测试行为和生产继承测试权限。禁止全测试豁免。

graph以实际运行依赖为真相。移除已证实失效边，必要真实依赖精确声明；动态capability metadata须追踪resolver再判断。不扩大cycle allowlist；依赖环经职责/接口下沉或退役已无消费者的旧写入口解决。

## 全仓覆盖矩阵

逐一覆盖全部tracked自有源代码与实际入口，排除第三方依赖、生成产物和历史实验原始数据：

1. PM/CE/Director/QA角色能力、可读交接、持续执行及验证反馈。
2. HTTP/CLI/UI入口、唯一执行owner、取消/恢复与历史只读边界。
3. ToolSpec归一化、host权限、路径/symlink、命令隔离和真实effect。
4. KFS/统一存储、临时文件/缓存/截图、实例workspace绑定。
5. ContextOS实际provider request、身份/调用关联、保留、预算与冷读取。
6. Verifier/QA当前候选、实际入口交互、missing/failed/unknown语义。
7. 事实流、SQLite持久化、提交、幂等、部分失败及恢复。
8. 实例/进程/NATS资源、物理退出、结算、隔离与Launcher。
9. 实时单轨WebSocket、作用域与UI投影，Bench仅内部测试。
10. 重复/遮蔽实现、静默fallback、失效兼容层、类型与公共API一致性。
11. 所有已承诺能力、未关闭TODO/FIXME/豁免及文档/生成资产的当前真实性。

每格列实际owner、输入/输出、effect、相邻调用者、异常/失效条件、恢复和最小复现实验。已标历史的材料不改造成当前事实。长记忆、未约定业务功能及无法因果关联的新研究不塞进本轮前置条件。

## 文件与协作

共享catalog、governance checker/schema、cycle资产由Root/单一集成者串行写。FactStream源码及专用测试独占一桶；跨Cell调用按文件/owner分桶；测试authority模型单独一桶。所有审计和临时文件在所属项目.polaris/KFS/UTF-8。保护main和其他有效服务，不编辑生成Bench目标。

## 验证和停止条件

逐批原例红/修后绿、scope回归与独立审阅；最终catalog hard-fail issue_count=0、manifest mismatch=0，真实SCC明确闭合而非扩容豁免。执行相关静态检查、完整KernelOne、前后端/架构回归与必要真实进程/浏览器实验。失效原测试需恢复有效语义，不能删掉失败。

全矩阵必要接受标准对应证据后冻结源码/配置/模型/口径。然后新隔离L1-01先验证零代修闭环，再按目录串行推进120项；每项保留全部尝试、成本未知、失败和Root质量判断。单项目通过不扩大为平台无边界完成。
