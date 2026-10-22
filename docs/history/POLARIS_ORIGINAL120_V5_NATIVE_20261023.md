# 原目录120项：v5真实运行、亲检与机制反馈

2026-10-23。外部工程记录，不是产品事实源。源码 `ab592bdf6b10b31e362e463e9f3775b2c4fe6db0`、原catalog SHA `d3222aaa1b6d490b92c3fc3db0e7fa84def340f39b5416e1889b805befa14999`、模型配置 SHA `0c7b50d666ec74e96832d69d0452a18d92551df14546bad7355eb2c366ae8436`。9469份源码与固定输入在两次运行中未变；正式PM→CE→Director→独立QA，串行独立实例，无外部代修、代装依赖、代跑原生门禁或补造收据。主实例/NATS未重启。

## 当前结果

| 项目／run | 原生结果 | ROOT亲检 | 资源 |
| --- | --- | --- | --- |
| L1-01／26d10955e0b64e958203c9b597a6c2dc | interrupted；verification passed；QA unknown；ProjectOutcome unknown | FAIL：3个major根因组 | pending0，实例与物理端口关闭 |
| L1-02／a5a177a3d73d4785a5697c2cb3027d5b | cancelled；verification passed；QA unknown；ProjectOutcome unknown | PARTIAL：基本创作循环成立、2项medium保留 | pending0，实例与物理端口关闭 |

本批2项尝试、0项有效交付、118项未启动。不能把旧catalog、特殊鹈鹕项目、组件测试或平台本次build通过算入当前120交付。源码／配置改变后下一次运行必须另起冻结批次。

L1-01在原5400秒预算结束后中断。L1-02因同一检查命令／结果精确重复111次而通过正式cancel接口收尾，原5400秒配置未改；不是超时，也不是模型无法执行的证明。保留全部失败、取消、UNKNOWN和未知成本。两次父流程分别约5467.45、1842.90秒，包含资源收尾；不能当成物理模型调用时长。

## 产物质量

L1-01真实检查／build通过，第一次QA指出花朵心情操作问题，反馈进入同Director并自主修复，未重做PM/CE。当前revision107的8个验证命令通过。第二次QA未完成，不能用先前拒绝或成功的命令代替当前验收。

ROOT在原生资源关闭后，对原字节页面做实际操作；保存78张原始PNG、亲看13张，源码／输出前后相同。发现：

1. Start替换了panel内Garden，但动画frame继续引用旧Garden，虫数／种子／重播未作用到真实场景。这是一个状态分叉根因，不拆成多个独立缺陷。
2. 清空seed后 `Number("")` 转0，错误输入未被阻断，破坏原有效状态。
3. 暂停时moon/mood直接integrate，未等下一步即影响当前状态。

花朵选择／心情应用的修复可见，初始动画与暂停／单步可运行；完整10秒闪烁因果序列仍未证明。ROOT没有修改目标来帮助通过。

L1-02的35个核心断言、23个fakeDOM断言及8个验证命令通过，均为原生历史证据。ROOT未代跑这些门禁，另以真实浏览器检查桌面／390px手机、三种配方、1/5张便签、1/80/81字、空格、删除、仪式和两次实际下载。保存38张PNG、亲看8张；两个1361字节UTF-8下载均匹配预览标题／正文／来源／仪式及完成状态；有效作品在错误输入后保留、重新炼制重置仪式。源码／输出与已知ROOT资源均闭合。

两张指定便签在拟人／拼接正文中有实际意象，不只抄进来源区。三种配方确有区别；它不是只有一行说明的空壳。但仍保留两个medium：纯空格错误仅出现在表单底部，未贴原便签行；反转UI承诺意象反义，样本实际复用原意象、只倒转字符。后者是产品承诺与实现偏差，原catalog并未指定必须采用某种反义算法。卡片初现的低透明度来自0.4秒CSS动画，已另观测稳定帧，不误报成持续不可读。

## 首次偏差与有限修复

| 观察事实 | Owner／边界 | 可证伪机制与本次处理 |
| --- | --- | --- |
| 最终tools丢约束、默认capture/revisit误拒 | K1 ToolSpec producer／有效快照／重建 | 保留完整schema和bool/list required语义；仅纯builder修复不够。独立审查另发现顶层union先按legacy `.lower()`解释，修复为先尊重权威parameters；两协议真实注册、JSON冷恢复回归 |
| L1-02自然fill被拒、四动作无法完整创作 | roles.runtime browser capability | 补fill/select_option和完整原子操作序列；保留原K1 sandbox、deadline、等待／帧／wire上限、权限与取消排空。不宣称跨调用状态或下载能力 |
| 大请求估算开销；无图片时工具参数未进入输出预算 | K1 estimator及invoke/stream | CJK等价优化；同一结构化输入成本同时用于准入和有效输出，两消费者一起迁移，不抬模型窗口或输出配置 |
| QA收到完整反馈仍重复检查 | 既有QA循环／当前请求投影 | 两个固定历史body、A/B及B/A共4次真实HTTP；B仅追加host短期事实。L101两组都重复，L102原臂重复、事实臂选择browser。只支持候选下一步变化，不证明收敛、执行成功或交付收益 |

ToolSpec、预算和浏览器修改的原反例、正负控制、恢复及最终请求验证见[蓝图](../blueprints/POLARIS_NATIVE_TOOL_CONTEXT_CLOSURE_20261023.md)与[实施计划](../superpowers/plans/2026-10-23-native-tool-context-closure.md)。模型实验停止于预登记4次，不补样、不执行返回工具。新QA事实只从现journal派生并追加当前请求，不持久化成第二状态，不删历史、不强迫验收、不增加拒绝／重试规则。完整效果留给新冻结原生运行。

## 证据与适用范围

主仓 `.polaris/bench/original120-20261023-v5/` 保存driver、输入、source-freeze、逐case报告及freeze-closed。目标根为 `/home/dains/Documents/polaris-bench-projects/original120-20261023-v5/{L1-01,L1-02}`，各自 `.polaris/audits/root-personal-quality.json`、`root-runtime-archive/`、首败与QA收敛切片保留原始请求／工具反馈／PNG。

L1-01归档586份raw，L1-02归档624份raw，均无缺失或读取错误；这些是prepared／terminal相位文件，不是物理请求数。工作状态pilot另有4次实际HTTP200响应且客户端关闭；该次数只能用于这个operator。平台全部HTTP、真实视觉token、账单和人工分钟仍UNKNOWN。成功交付为0，单次成功成本未定义。

保留PM/CE专业分工与普通交接、原任务内反馈修复、独立只读视觉QA、最终请求审计、K1隔离／KFS、耐久日志与资源owner。TaskMarket派发／领取、依赖、Scout、专业能力、恢复实验与ContextOS策略比较继续按实际项目缺口推进；不因简化旧合同而否定这些能力，也不在没有数据时全量恢复。长期记忆仍不做。下一步是集成并冻结一个新候选，全新L1-01完整交付及亲检成功后依原120逐项扩大。
