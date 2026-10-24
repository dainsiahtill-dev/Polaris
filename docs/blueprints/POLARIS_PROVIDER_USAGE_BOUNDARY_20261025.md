# Provider usage边界：缓存数量与累计输入

本项修复由L2-14真实运行暴露：活动Anthropic兼容provider和native capture各有一处字段丢失。它只收敛用量接口，不改模型、请求、工具权限、重试、deadline或项目完成判定。

## 事实与可证伪解释

SourceDF3任务 `6d51da0262c241038fd624920378875d` 的真实回应1705，原始usage为input816、output725、cache_read143597、cache_creation0；归一化却是prompt816、cached0、total1541，capture的原始投影也丢了两个缓存字段。原始provider_response仍完整，旧SQLite/CAS不改写。

已有KernelOne归一化器和协议语义要求prompt144413、completion725、cached143597、total145138。缓存读取是累计处理量的子集，不是新唯一上下文，也不能直接作为金额。[MiniMax字段示例与适用模型](https://platform.minimax.io/docs/api-reference/text-prompt-caching)、[Anthropic计数定义](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。

第一偏差owner是 `infrastructure.llm.providers.anthropic_provider._usage_from_response`，回调重写了输入和输出映射而遗漏缓存；第二偏差owner是 `NativeRequestCapturePort._usage_evidence` 的有界字段白名单。二者在同一现有链路收尾，不能只修UI。

## 机制

- 活动provider复用公共 `ResponseNormalizer.normalize_response`，传入仅usage的payload，保留原字符数及缺失/无效时原有估计路径；不重新解析实际内容和工具action，不建另一套映射。
- 现有capture白名单保留有效非负整数的原始cache_read/cache_creation，显式0仍为0，缺失键保持缺失。
- `Usage.cached_tokens` 的默认0不是独立报告。若无真实缓存读取数量且归一化值仍为0，capture省略该字段；缓存写入不证明读取为0。正数与明确报告的0保留。
- 原始数据字典不变；持久化仍是原SQLite/CAS，现有ContextOS和CLI消费修正后的事件。历史错误投影保留为历史事实，显式只读重算另列，不升级成新原生收据。
- OpenAI inclusive prompt与cached子集不重复相加。本改动不声称修好了活动OpenAI provider自身的缓存遗漏；该独立缺口已记录。部分流式usage也不在当前全面能力声明内。

## 实验与接受标准

修改前两个真实路径反例失败、非缓存正控通过：实际AnthropicProvider.invoke回调，以及实际loopback HTTP的AIExecutor/capture/CAS/terminal观察。不是以手工正确Usage替代provider路径。

初候选207项相关测试通过后，独审用实际生产者补出bool/负数缓存被误算的反例。新候选在活动回调中先校验新缓存计数，再复用归一化；畸形计数走已有estimated路径、保留原始回应，不产生虚假的reported算术。十项失败反例/五项正控先封存，再复测25项针对性场景通过；最终相邻220项通过，类型/格式通过，包含真实producer到canonical SQLite、冷服务、ContextOS/API和CLI的合成协议路径。缓存创建正数为明确合成控制，实际M3样例创建量为0。ROOT另外将185份实际原始回应逐一经新活动回调及capture函数只读重放，数值与独立公式一致，未改历史事实；这仍不是新模型调用。完整发布检查与独审在当前Source收尾，不冒充新版本原生项目交付。

一项历史流式测试在HEAD和候选中都因轻量aiohttp模块缓存污染失败；原失败、对照均保留。拥有真实HTTP断言的测试显式重置该合成缓存后导入真实aiohttp，不放宽断言或跳过测试。另一个原文件Optional类型问题以测试内非空断言收紧，基线诊断保留。

接受标准：原反例GREEN、非缓存/创建/0与缺失/OpenAI无重复计数控制成立，真实相邻生产消费与冷读不丢字段，原字典和旧快照不变，物理POST和费用继续UNKNOWN；再完成规定发布检查和独审，冻结新Source用于下一实际任务。新风险记录后续队列，不把本主题扩为全provider/定价/telemetry重构。

若活动回调原本已有正确数值，首个解释即被推翻；若capture或冷读仍掉字段、把缓存重复加到inclusive prompt、补造未报告0、由快照推POST/账单，则迁移未完成。

## 已知边界

API字段的报告量不是供应商账单，也不等于真实物理发送次数。当前候选只关闭活动Anthropic兼容边界与capture，不承诺其他provider、历史记录或任意部分流式协议已全部收敛。组件通过不修复目标项目的视觉回归，也不证明Polaris优于其他Agent。实验原始材料在 `.polaris/audits/provider-cache-usage-20261025/`，新版本原生消费需另列。
