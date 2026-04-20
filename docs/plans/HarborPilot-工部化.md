Polaris「工部化」完整借鉴计划（含 ChiefEngineer/Director/UI-Director）
摘要
目标：把 Polaris 从“PM→(CE可选)→Director”升级为“PM→工部路由→Director 家族执行”，默认仍走 Director，但可按任务类型自动分流到 UI-Director，并保留 ChiefEngineer 作为高复杂任务的前置技术蓝图。
价值评估：对 Polaris 的综合价值为 高（8/10）；对 UI 密集型任务价值 很高（9/10）；对纯后端小任务价值 中等（5/10）。
核心借鉴点：借鉴 HolonPolis 的“可复用代码资产索引 + Hybrid 检索 + 指纹增量更新 + Prompt 上下文构建”能力，不借鉴其“自演化造物”机制，避免系统复杂度失控。
架构原则：保持 Polaris 元工具定位，不引入任何目标业务代码；所有文本读写显式 UTF-8；优先根因重构（去硬编码角色分支），不做补丁式拼接。
现状基线（用于映射改造）
Polaris 已有 CE 蓝图链路，可直接复用：
chief_engineer.py
orchestration_engine.py
worker_executor.py
Polaris 角色系统“配置可扩展、执行硬编码”并存：
core_roles.yaml
registry.py
role_dialogue.py
Polaris 代码检索目前是 LanceDB 存储 + Pandas 文本匹配，未形成真正 Hybrid 检索：
code_search.py
HolonPolis 可借鉴实现（已验证具备闭环）：
ui_component_library_service.py
reusable_code_library_service.py
memory_service.py
lancedb_factory.py
目标架构（决策已定）
新增“工部路由层”（Works Department Router），由它统一决定：是否启用 CE 前置、由哪个 Director 执行。
Director 家族首批定义：director_general（兼容现有 Director）、ui_director（UI 专长）；后续 director 以配置注册方式扩展。
ChiefEngineer 保留且默认 auto，仅在高复杂或跨模块任务触发；简单任务直接 director_general，不增加延迟。
新增“可复用资产知识平面”：专门为 Director 家族服务的 LanceDB 资产库（UI 组件库、通用代码库），与运行工件隔离存放在 Polaris 受控路径下。
UI 任务走“检索增强执行”：UI-Director 执行前自动构建“组件/样式/用法”上下文块注入 Prompt。
兼容策略：老任务 assigned_to: Director|ChiefEngineer 不变；新字段逐步启用，灰度期间双写双读。
安全基线同步升级：修复命令白名单 startswith 风险，改为 token 级精确匹配；外部库索引只读、不执行、UTF-8 校验、路径边界校验。
公共接口 / 类型 / 合约变更
RoleProfile 新增字段（向后兼容，默认值已定）：
role_group: str = "core"；specializations: List[str] = []；dispatch_priority: int = 100；supports_preflight: bool = False；knowledge_policies: List[str] = []。
改动文件：
schema.py
registry.py
core_roles.yaml
任务合约扩展（pm_tasks.contract.json）：
新增 execution_profile 对象：department、selected_director、requires_preflight、knowledge_queries；保留 assigned_to 作为兼容字段。
改动入口：
task_helpers.py
tasks.py
新增工部路由内部类型：
DepartmentDispatchDecision、TaskSignals、DirectorCandidate、PreflightDecision。
新增文件：department_router.py
API 扩展（挂载到 Arsenal Router，复用现有风格）：
POST /arsenal/code/library/index；POST /arsenal/code/library/search；POST /arsenal/code/library/context。
改动文件：
arsenal.py
分阶段实施（6 阶段，决策完整）
Phase 0：基线与开关（1 周）
增加功能开关：POLARIS_WORKS_DEPT_ENABLED=0、POLARIS_UI_DIRECTOR_ENABLED=0、POLARIS_KNOWLEDGE_HYBRID_ENABLED=0。
增加观测指标：任务一次通过率、返工轮次、平均时延、UI 任务命中率、检索命中率、Token 消耗。
完成标准：不改现有行为，所有新增代码在开关关闭时零影响。
Phase 1：根因重构角色硬编码（1.5 周）
将 role_dialogue.py 从固定角色分支改为“模板+校验器注册表”机制，去除对 pm/architect/chief_engineer/director/qa 的硬编码判断。
将 PM 节点的“ChiefEngineer/Director 二选一”改为调用工部路由结果。
将 SUPPORTED_ASSIGNEES 从静态常量改为“核心角色 + director_family 动态集”。
完成标准：新增角色不改核心代码即可注册生效；旧角色行为回归一致。
Phase 2：工部路由落地（1 周）
新增 department_router.py，路由规则固定如下：
ui_score >= 3 选 ui_director；否则 director_general。
complexity_score >= 0.65 或 cross_module_count >= 3 或 target_files > 8 时启用 CE preflight。
orchestration_engine.py 接入新决策对象，统一写入 execution_profile。
worker_executor.py 读取 execution_profile 并选择对应 Prompt 模板与知识上下文。
完成标准：默认流程仍可只走 Director；CE 能被按规则自动跳过或触发。
Phase 3：知识平面与 LanceDB Hybrid（2 周）
新增代码资产服务（借鉴 HolonPolis 但按 Polaris 结构实现）：
code_library_service.py、ui_library_service.py、hybrid_search_service.py。
LanceDB 表结构定稿：
knowledge_libraries（库元数据、license、source_hash、fingerprints）；
knowledge_assets（asset_id、library_id、relative_path、content、embedding、tags、content_hash、created_at）。
检索策略定稿：向量 0.55 + FTS 0.45，加词法加权；结果按 content_hash 与活跃指纹表去陈旧去重。
完成标准：同一库重复索引可增量复用；搜索结果稳定可复现。
Phase 4：UI-Director 执行增强（1 周）
UI-Director Prompt 模板新增“组件约束、设计系统一致性、响应式与可访问性检查项”。
执行前自动调用 build_prompt_context(query, top_k=3, max_code_chars=3000) 注入上下文。
若 ui_director 不可用或无命中，自动降级 director_general 并记录降级原因。
完成标准：UI 任务可稳定读取外部组件库知识并生成可执行改动方案。
Phase 5：安全与合规加固（0.5 周）
修复命令白名单判定，替换 startswith 为首 token 精确匹配 + 参数校验。
改动文件：
tooling_security.py
索引前做 license gate：默认仅允许 MIT/Apache-2.0/BSD，未知许可证仅可“参考检索”不可“模板输出”。
路径与编码强制：仅允许受控根路径、读取文本显式 UTF-8、超大文件与二进制文件拒绝索引。
完成标准：安全测试全部通过，且不影响正常执行链路。
Phase 6：灰度发布与回滚（0.5 周）
灰度策略：10% UI 任务开启 ui_director，一周后达标再升到 50%。
达标阈值：UI 一次通过率提升 >= 20%，平均返工轮次下降 >= 15%，P95 延迟不劣化超过 10%。
回滚条件：任一核心指标连续 2 天劣化超阈值，立即关闭对应开关并回退到 director_general。
完成标准：具备秒级配置回退能力，且历史任务可继续执行。
测试用例与验收场景
角色扩展回归：新增 ui_director 后无需改 role_dialogue 主流程即可完成注册、对话、校验。
路由正确性：UI 文件任务命中 ui_director；纯后端任务命中 director_general；高复杂任务触发 CE preflight。
增量索引正确性：重复索引同库，reused_assets 增长且不重复写入。
检索质量：同查询在固定库快照下返回顺序稳定，且陈旧 hash 结果被过滤。
Prompt 注入边界：上下文总长度受 token/char 限制，且不会覆盖核心 system policy。
兼容回归：旧 assigned_to 任务可无损执行；历史 contract 可读。
安全测试：路径穿越、非法编码、恶意命令前缀绕过均被拒绝。
性能测试：10k 文件库索引与检索在可接受时延内；并发检索无崩溃。
端到端场景：PM 生成 UI 任务→工部路由→UI-Director 执行→结果回写与审计完整。
借鉴范围边界（明确不做）
不引入 HolonPolis 的“自主孵化 Agent/自演化技能写入主仓”机制。
不在 Polaris 主仓保存任何目标项目业务代码或第三方项目完整快照。
不让外部库索引过程执行任意代码，仅做静态读取与结构化记忆。
假设与默认值（已锁定）
默认流程保持 Director 主路径，ChiefEngineer 为 auto 可选前置，不强制每任务执行。
首版仅支持 React/Vue UI 资产特化；其他框架先按 code_asset 处理。
外部学习源默认是本地目录（用户已下载），不直接在服务端自动联网抓取仓库。
所有新增文本读写一律显式 UTF-8；JSON/日志/Markdown 均执行一致规则。
先落地“配置驱动扩展能力”，再考虑更多 Director 细分，不提前扩表复杂度。