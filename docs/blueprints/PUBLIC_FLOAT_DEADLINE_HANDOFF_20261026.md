# 执行 deadline 浮点公开交接

状态：跨Cell私有引用债务的局部接口迁移，尚未实施。现roles.adapters/director/execution直接读取RoleKernel私有deadline解析/选择helper。既有public整数clamp至少1秒且取整，不能直接替换adapter现有float/subsecond语义。

在RoleKernel现有public.llm_caller_contracts增加窄float入口，复用同owner的解析/viable选择和错误类型；旧整数入口原样保留。新入口等价现adapter：无deadline时max(0.1,float(timeout))；可行deadline时取min(该timeout,remaining)，即剩余小于0.1也不能扩张期限；无效/耗尽保留现error code和Context取值优先级。是否已有可复用完全等价API必须先核对。

然后adapter调用此真实public入口，清除私有import与跨Cell私有monkeypatch。public producer、实际consumer和测试同时迁移；不新增controller、预算、重试、超时或字段给模型。

冻结时钟金标先红后绿：12.75无deadline、0timeout->0.1、100.25剩余->0.25、0.125原预算、剩余0.05不扩张、expired/invalid/bool/nonfinite、wave/run viable优先级与字符串解析。旧整数0.5秒拒绝、2.75取整2及返回类型不变。真实public factory构造adapter，验证值/拒绝正确传递而不调用模型/工具；caller冷导入只用公开接口。

Root同步metadata，不扩大allowlist，不改历史事实。所有fixture在.polaris，源项目与服务不可触碰。达到合法推进与错误拒绝、旧行为等价和相邻测试通过即停止本主题，大型图收敛另行继续。
