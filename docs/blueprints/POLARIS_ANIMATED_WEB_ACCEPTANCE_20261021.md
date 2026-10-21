# Polaris 当前工程能力收敛与动画网页验收

状态：实施中。用户授权：依据可行性、可靠性、稳定性落地；最终用“鹈鹕骑单车”网页动画项目验证；长期记忆明确暂不做。保留已批准的角色Agent重构，直接在main工作，不建分支/worktree。

## 目标与边界

通过现有工程owner完成真实PM→CE→Director协作、工具执行和持续修复、当前候选验证、独立QA、最终网页质量检查及资源关闭。模型自主决定工程实现，不维护合同hash/义务覆盖图，不引入旧TurnEngine/TransactionTurn写入口或新的事实总账。

本轮包含通用命令环境契约修复、当前请求事实正确性核查、版本独立的动画网页内部案例、冻结后正式执行与实际浏览器验收。不实施长期身份/长期记忆/经验学习，不因本例重新接入StateFirst旧owner，不承诺单例能证明整个平台或优于其他工具。

## 已知事实与第一偏差

静态SVG第二例中，模型三次命令向/tmp/gen.py写入/追加。每次命令均新建KernelOne bwrap；/tmp、HOME、cache、output使用私有tmpfs且跨调用重置。前两次本身成功，第三次缺少此前定义。最终物理tools描述未公开该生命周期。timeout实际只接受1–120秒，物理schema仅default30。第一边界偏差在roles.runtime向provider公开的执行契约，并非需要扩大授权或增加重试。

证据：~/.polaris/audit_archives/autonomous-delivery-20261018/engineering-v2-refactor-20261020/task13-pelican-benchmark/tool-contract-audit/report.json。旧静态结果保留，不追改为动画结果。

## 结构与数据流

用户目标/host权限/预算 -> EngineeringRunService（唯一owner、SQLite持久记录） -> PM普通文本 -> CE完整目标与规划/普通文本设计 -> Director native工具循环 -> ToolSpecRegistry归一化 -> KFS/KernelOne sandbox实际效果 -> 原工具结果回同任务 -> 当前输入Verifier -> 当前候选独立QA -> ProjectOutcome与实例释放。

ContextOS继续保存最终provider request及同workspace可读引用。本轮不把审计视图等同于语义上下文收益。当前历史持久化、身份和失败回灌先复用，发现直接影响当前任务的缺陷才局部修改。

## 模块职责与最小改动

- roles.runtime / EngineeringTools.schemas：在现有派生schema中准确说明shell、cwd、每调用私有临时目录生命周期、授权workspace持久化和真实network状态；公开timeout的默认值和范围。执行验证与schema共用模块内常量。仅改技术契约，不改变挂载、权限、deadline、取消或恢复语义。
- ToolSpecRegistry：仍是工具名/参数归一化唯一来源，返回schema副本。不要修改全局registry以描述某会话的动态权限。
- KernelOne sandbox/process：复用现有containment和物理drain；不新增持久scratch/控制器。非法调用拒绝，合法workspace持久写入与随后读取均须成立。
- 当前ContextOS审计flags：审查已知JSON字符串/工具反馈误判是否能独立最小修复；不得作为动画任务新前置研究项。
- 内部案例：新增独立VIS-PEL-WEB-01元数据/需求与评估标准；原120项及静态VIS-PEL-01字节不变。主仓不添加目标HTML/SVG/业务实现。

## 可靠性实验

1. RED：真实provider编码后的工具schema缺少当前环境说明及timeout上下界。
2. 真实两次命令：首次分别写私有/tmp和授权workspace；第二次只保留workspace文件。换冷对象/历史读取不改变持久文件；显式空写权限不能借提示扩大。
3. 归一化后的合法命令继续执行，超范围timeout明确拒绝，所有观察可持久化/读回；测试仅替换远端模型，实际HTTP/codec/sandbox/SQLite均真实。
4. GREEN后运行相邻角色/验证/恢复/取消测试、类型/lint及必要KernelOne/catalog门禁。不得将合成provider组件实验称为真实模型交付。

## 动画网页验收

通过正式隔离入口、现有唯一owner启动。首例用真实collaborative模式，PM/CE建议完整传递；Director可保存简短可读方案并实现，不加业务协议准入。模型/配置/源码/需求/预算/检查在整个运行稳定。原始任务文件由Polaris合法工具自行生成，外部不改代码、不代装依赖、不补收据。

成品应可离线打开，是可辨认的鹈鹕骑自行车网页，具有真实持续动画、轮子与脚踏/腿的协调关系、可工作的播放暂停和速度交互，并在常见窗口可完整观看。允许任何合理艺术风格，不按元素数、代码行数或固定几何打分。短说明解释打开方式与验证，不要求大型文档。

必需检查仅约束实际入口/本地依赖与功能，不塞入软件工程项目的通用文件/行数配额。若当前环境可在相同sandbox中启动已有浏览器依赖，host显式声明只读根并先做合成可行性实验；不得声称截图已经成为模型视觉输入。原生QA判断当前需求/源码/真实工具观察，root另外亲自用真实浏览器检查作品。二者证据分别记录。

外部浏览器只读取原产物，不修改目标或代写原生验证事实。检查实际加载、console/page错误、离线资源、动画帧变化、暂停恢复/速度、空间关系与视觉质量；录像/截图与原始字节绑定。缺少/失败/未运行/UNKNOWN分别报告。最终同时核对原生完成、当前QA、root质量和资源关闭；失败则封存第一证据，再决定平台修复或同任务合法反馈，不热改运行中源码。

## 停止与接受

每个修复满足原反例、修后通过、相邻合法/非法/恢复路径与必要门禁，即结束该问题研究并集成。源码冻结后新跑。网页通过只证明这项任务在本次版本/环境/预算内成立；角色最优组合、长期稳定性、120项覆盖和竞品优越性另需重复/对照，未知费用和物理发送次数保留UNKNOWN。
