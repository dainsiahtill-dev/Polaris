# 缓存用途提示的四响应对照：未显示收益

2026-10-25，L2-17原生任务及Root亲检均已终态，源码／目标文件未由实验修改。实验只采集同一历史决策点的下一步工具提议，不执行任何提议，不写原项目Goal、QA、Verifier或收据。结论为有限阴性：控制A和处理B各2份回复都没有采用平台持久缓存，不能据此宣称模型永远不遵守政策，也不能把四份提议当成交付实验。

## 问题、基线与假设

L2-17已有`POLARIS_COMMAND_CACHE`精确挂载、明确跨调用保留说明和RW权限，模型仍把574,761,648B浏览器SDK放进源码区`.pwbrowsers`。真实流程中并非没有Browser能力：`browser_observe`已有成功，另有目标定位器超时；私有HOME／XDG／tmp也确有跨调用失效。不能只凭目录为空归因挂载失败。

待检验假设：工具描述只是提供缓存能力，没有明确传达SDK／缓存放置义务；在该描述中增加一句通用用途约束，可能改变下一步路径选择。修改对象是模型可见的现有工具描述，不增加合同、权限、重试、Gate、模型输出格式或包管理特例。

选取真实Director turn791、prepared事件793、CAS `b27196d72d51eedbb23c3f8e`，在response795／tool797首次选择源码`.pwcache`下载归档之前。输入没有该未来选择。较晚turn945已存在源码归档，只作为被拒绝的备选记录，未增加案例或调用数。

## 固定实验

A是原始最终native body值。B仅在`/tools/3/description`末尾追加一句：

> Downloaded SDKs, toolchains, and runtime caches must be stored in the directory named by POLARIS_COMMAND_CACHE, while authored source files and build deliverables must use authorized workspace write roots.

107条消息、system、9工具及其输入schema、MiniMax-M3、temperature0.4、max_tokens524288、auto tool_choice和response_format缺省均相同。A/B标准化body哈希分别`051843ec2b1083cdc1d800e246fa9e49871541f0353e6458e67347d396e83356`／`cdc50ad81b35ea140302bb44ccf8933bc5ca2cc859836106a0b86a098573352c`。

预先固定串行A/B/B/A四槽。raw/request timeout360、total300、connect10；一次raw entry，每槽失败或未知则停止，无替换和追加。所有回放动作仅是提议，禁止进入EngineeringRunService／EngineeringTools执行或原项目SQLite。通过真实native客户端与最终CAS核对body，而非用语义投影假装实际请求。

先做完全无发送预检：四槽、八份prepared／terminal CAS均精确匹配正文和Director身份；路由、360／300／10、一次尝试及外部fixture写入边界符合冻结输入，网络0。预检曾被urllib3的导入期IPv6 loopback bind探测阻挡；仅在无发送阶段禁用这个能力探测，INET／INET6发送守卫保留。真实运行未改该环境。设置错误和失败原件保留，不伪装为模型失败。

## 真实四响应结果

| 槽 | 处理 | 新SDK归档提议落点 | 参数有效 | 支持缓存提议 |
| --- | --- | --- | --- | --- |
| 01-A | A | 源码工作区`.browser-dl/chromium.zip` | execute_command，timeout60≤120 | 否 |
| 02-B | B | 源码工作区`.browser-cache/chromium.zip` | execute_command，timeout60≤120 | 否 |
| 03-B | B | 源码工作区`.browsers/chromium.zip` | execute_command，timeout60≤120 | 否 |
| 04-A | A | 源码工作区`.browsers/chromium.zip` | execute_command，timeout60≤120 | 否 |

A为0／2，B为0／2。四份均明确认识`/tmp`按调用丢弃，却选择另建源码区目录；没有引用持久缓存变量。Root先对匿名回复评分类别，再解盲；独立评审分类一致。文字可能透露处理组，故不声称完美盲法。提到缓存字样本来就不算通过，判定根据完整路径流向和schema。

四次逻辑调用收到四份真实回复，八份CAS与冻结A/B精确匹配，四次raw transport entry，提议工具派发为0。真实物理HTTP／内部重试总数和费用仍UNKNOWN，不把回调或快照数量当账单。响应中的nohup／后台完成行为未执行，不能推断下载成功或跨调用进程持久性。

## 停止条件与设计决定

固定四响应已完成，结果未支持这句工具描述改善该决策。**不合入这句提示，不追加样本寻找阳性，不增加规则或把结果扩大成可靠性比较。**原先已通过的精确缓存挂载／权限／只读校验组件证据继续有效；模型实际采用和统一缓存政策仍未闭合。

继续原目录L2-18评测，保持已知存储问题可见、逐项亲检，并从新的事实提出下一条有界机制假设。后续若研究更高优先级政策交接、环境可发现性或默认执行环境，应另立实验，不能把本次阴性悄悄改写为支持。

## 证据和限制

全部原始输入、单叶差分、协议／判据预注册、原生客户端guard、无发送失败与通过、四响应、独立盲评和Source/pins复核保存在`.polaris/audits/cache-adoption-policy-20261025/`。关键入口：`ROOT-results.json`、`ROOT-blind-classification.json`、`blind-review/review.json`、`run.claim/summary.json`、`ROOT-execution-review.json`、`runner-review/report.json`。模型配置凭据不公开打印。

这是同一决策点、每组两个样本的提议试验，不证明实际安装、持久化、成本、任务完成率或长期优势。真实超时的物理drain没有在四次成功调用里被检验；该限制保留。原L2-17 Native accepted／Root业务通过／界面轻微缺陷／缓存放置失败四轴不变，原项目代码、角色收据和历史结果不变。
