# kernelone/llm

KernelOne 的 LLM 运行时内核，只承载与业务无关的技术能力：

- 角色绑定调用治理（strict / fallback）
- provider type 归一化
- timeout / blocked provider 策略
- API key 环境变量解析策略
- 工具调用统一运行时（parse / policy / execute / feedback）

禁止放入具体厂商 SDK、HTTP 细节、业务用例提示词。
