# Cognitive SLM Architecture — 认知协处理器架构

> 文档日期: 2026-04-19
> 所属模块: `polaris/cells/roles/kernel/internal/transaction/`
> 状态: 已落地

---

## 1. 为什么需要 SLM（Small Language Model）

### 1.1 核心矛盾

| 问题 | 主模型 (Claude/GPT-4) | SLM (本地/局域网小模型) |
|------|----------------------|------------------------|
| 延迟 | 100-500ms / token | 10-50ms / token |
| 成本 | $$$ 按 token 计费 | $ 电费即可 |
| 可用性 | 依赖公网/API | 局域网/本机 100% 可控 |
| 能力 | 通用推理、代码生成 | 分类、降维、格式修复 |

主模型负责**架构决策和核心代码编写**；所有"洗菜、切菜、倒垃圾"的边缘计算任务交给 SLM 协处理器并行处理。

### 1.2 具体场景

1. **意图分类 refine**: Regex + Embedding 仍无法确定意图时，本地小模型做最终裁决
2. **长日志降维**: pytest 失败日志、编译错误冗长，SLM 提炼为 1-2 句核心摘要
3. **JSON 语法修复**: tool call JSON 漏逗号、多括号等常见幻觉，SLM 自动修复
4. **搜索查询扩展**: 将模糊自然语言翻译为精确关键词列表，增强 `repo_rg` / `search_code`

### 1.3 设计哲学

> "主模型只负责架构决策和核心代码编写；所有洗菜、切菜、倒垃圾的工作交给 SLM 协处理器并行处理。"

---

## 2. 整体架构

```
用户请求
    |
    v
+---------------------------+     +---------------------------+
|  CognitiveGateway         |     |  IntentEmbeddingRouter    |
|  统一认知网关              |     |  (背景 daemon 线程 warmup) |
+---------------------------+     +---------------------------+
    |                                       |
    |  级联瀑布 (Waterfall)                  |
    v                                       v
Level 1: Embedding cosine similarity  -----> 零延迟 fast path
    | (未 warmup / 阈值不足 则降级)
    v
Level 2: SLMCoprocessor (Ollama@120.24.117.59:11434)
    | (健康检查失败 / slm_enabled=false 则降级)
    v
Level 3: classify_intent_regex (硬编码正则)
    | (100% 可用终极兜底)
    v
标准意图标签: STRONG_MUTATION / DEBUG_AND_FIX / DEVOPS / WEAK_MUTATION /
             TESTING / PLANNING / ANALYSIS_ONLY / UNKNOWN
```

---

## 3. 模块清单

| 模块 | 文件 | 职责 |
|------|------|------|
| `TransactionConfig` | `transaction/ledger.py` | 认知层配置总开关 (`slm_enabled`, `intent_embedding_enabled`, `slm_base_url`, `intent_embedding_threshold`) |
| `IntentEmbeddingRouter` | `transaction/intent_embedding_router.py` | Phase 2 Hybrid Intent Routing — Regex first, embedding fallback |
| `SLMCoprocessor` | `transaction/slm_coprocessor.py` | 统一 SLM 调用门面，四大任务接口 |
| `CognitiveGateway` | `transaction/cognitive_gateway.py` | 统一认知网关：健康监控 + 级联瀑布 + 任务调度 |
| `classify_intent_regex` | `transaction/intent_classifier.py` | 硬编码正则意图分类 (100% 兜底) |

---

## 4. 级联意图分类 (Waterfall)

### 4.1 三层降级策略

```python
async def classify_intent(self, message: str) -> str:
    # Level 1: Embedding Router（centroids ready 且启用）
    if embedding_router.classify(message) hits threshold:
        return emb_result

    # Level 2: SLM Coprocessor（健康且启用）
    if is_slm_healthy():
        slm_result = await slm_coprocessor.classify_intent(message)
        if slm_result != "UNKNOWN":
            return slm_result

    # Level 3: Hard-coded Regex（100% 可用兜底）
    return classify_intent_regex(message)
```

### 4.2 各层特性

| 层级 | 延迟 | 准确率 | 可用性 | 触发条件 |
|------|------|--------|--------|----------|
| L1 Embedding | ~5-20ms | 中高 | 依赖 centroids warmup | `intent_embedding_enabled=True` 且 centroids ready |
| L2 SLM | ~50-200ms | 高 | 依赖 SLM 可达 | `slm_enabled=True` 且健康检查通过 |
| L3 Regex | ~0.1ms | 中 | 100% |  always |

### 4.3 健康检查

- 30s TTL 缓存，避免频繁探测
- 探测方式：极轻量 SLM 调用 (`prompt="hi"`, `max_tokens=1`)
- 手动失效接口：`gateway.invalidate_health_cache()`
- 配置项：`TransactionConfig.slm_enabled`, `slm_base_url`, `slm_timeout`

---

## 5. SLMCoprocessor — 认知协处理器

### 5.1 四大任务

```python
class SLMCoprocessor:
    async def classify_intent(text: str, categories: list[str] | None = None) -> str
    async def distill_long_logs(raw_logs: str, max_tokens: int = 500) -> str
    async def heal_json(broken_json: str) -> dict[str, Any] | None
    async def expand_search_query(user_query: str) -> list[str]
```

### 5.2 降级策略

| 任务 | SLM 可用时 | SLM 不可用时 |
|------|-----------|-------------|
| `classify_intent` | SLM 分类 refine | 返回 `"UNKNOWN"` (由 Regex 兜底) |
| `distill_long_logs` | SLM 提炼摘要 | 暴力截断尾部 2000 字符 |
| `heal_json` | SLM 修复 JSON | 返回 `None` |
| `expand_search_query` | SLM 扩展关键词 | 返回原查询 `[user_query]` |

### 5.3 Provider 接入

- 默认 provider: `ollama`
- 默认模型: `glm-4.7-flash:latest`
- 默认地址: `http://120.24.117.59:11434` (或 `OLLAMA_HOST` 环境变量)
- 所有同步 I/O (`requests.post`) 通过 `asyncio.to_thread` offload 到线程池

---

## 6. IntentEmbeddingRouter — 相位 2 混合意图路由

### 6.1 架构

```python
class IntentEmbeddingRouter:
    - 单例模式，后台 daemon 线程预计算 intent centroids
    - classify() 为 async：embedding 调用 wrapped in asyncio.to_thread
    - 纯 CPU cosine similarity 在获取 embedding 后同步计算
    - 失败时返回 None → caller 回退到 Regex
```

### 6.2 意图描述锚点

每个意图标签有 CN + EN 多组描述，用于提升 centroid 质量：

```python
INTENT_DESCRIPTIONS = {
    "STRONG_MUTATION": [
        "修改代码 创建文件 删除函数 重写逻辑 实现功能 写入文件",
        "modify code create file delete function rewrite logic implement feature write file",
    ],
    "DEBUG_AND_FIX": [
        "修复bug 排查错误 解决异常 调试程序 定位问题",
        "debug crash fix bug troubleshoot error resolve exception investigate issue",
    ],
    # ... 共 7 个意图类别
}
```

### 6.3 零阻塞冷启动

- 后台 daemon thread 在 `IntentEmbeddingRouter.default()` 时启动
- `classify()` 若 centroids 未 ready，静默返回 `None`
- caller (CognitiveGateway) 自动降级到 SLM / Regex

---

## 7. 配置说明

所有配置集中在 `TransactionConfig`：

```python
@dataclass
class TransactionConfig:
    # === SLM 协处理器配置 ===
    slm_enabled: bool = True
    slm_provider: str = "ollama"
    slm_model_name: str = "glm-4.7-flash:latest"
    slm_base_url: str = ""          # 空则使用 OLLAMA_HOST 环境变量或 localhost
    slm_timeout: int = 30

    # === 意图 Embedding 配置 ===
    intent_embedding_enabled: bool = True
    intent_embedding_threshold: float = 0.72
```

---

## 8. 测试覆盖

| 测试文件 | 用例数 | 覆盖范围 |
|----------|--------|----------|
| `test_intent_embedding_router.py` | 9 | Embedding warmup、分类匹配、阈值判定、降级、混合路由 |
| `test_slm_coprocessor.py` | 12 | 四大任务、禁用状态、配置传播、健康降级 |
| `test_cognitive_gateway.py` | 22 | 健康缓存、级联瀑布、任务模板、统一入口 |
| `test_transaction_kernel_facade.py` | 32 | TTC Facade 集成（全部通过） |

**总计: 79 个测试，全部通过。**

---

## 9. 关键代码路径

```
polaris/cells/roles/kernel/internal/transaction/
├── cognitive_gateway.py          # 统一认知网关（新增）
├── slm_coprocessor.py            # SLM 协处理器（新增）
├── intent_embedding_router.py    # Embedding 意图路由（新增）
├── intent_classifier.py          # 硬编码 Regex 分类（扩展 classify_intent_regex）
├── ledger.py                     # TransactionConfig 扩展 SLM/Embedding 字段
├── retry_orchestrator.py         # 重试编排器（已有，TTC proxy 适配）
└── tests/
    ├── test_cognitive_gateway.py
    ├── test_slm_coprocessor.py
    └── test_intent_embedding_router.py
```

---

## 10. 演进路线

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 纯 Regex 意图分类 | ✅ 已有 |
| Phase 2 | Regex + Embedding cosine fallback | ✅ 已落地 |
| Phase 3 | LLM-based 意图分类 (延迟敏感，已锁定) | ❌ 明确拒绝 |
| Phase 4 | SLM 认知协处理器（四大任务） | ✅ 已落地 |
| Phase 5 | CognitiveGateway（级联瀑布 + 健康监控） | ✅ 已落地 |

---

## 11. 使用示例

```python
from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import CognitiveGateway

# 获取单例
gateway = await CognitiveGateway.default()

# 级联意图分类
intent = await gateway.classify_intent("请修复这个 bug")
# 可能返回: "DEBUG_AND_FIX"

# 日志降维
distilled = await gateway.distill_logs(long_error_log, max_tokens=200)

# JSON 修复
fixed = await gateway.heal_json('{"tool": "read_file" "path": "main.py"}')

# 查询扩展
keywords = await gateway.expand_query("用户登录鉴权")
# 可能返回: ["auth", "login", "jwt_token", "authentication"]

# 统一任务调度
result = await gateway.execute_task("INTENT_CLASSIFY", "修改代码")
```

---

## 12. 设计决策记录

### 12.1 为什么锁定 Phase 2，拒绝 Phase 3 (LLM-based)

- **延迟**: LLM 调用 100-500ms vs Embedding cosine 5-20ms vs Regex 0.1ms
- **成本**: LLM 按 token 计费，Embedding/Regex 几乎零成本
- **确定性**: Regex 100% 可预测，LLM 有幻觉风险
- **ROI**: Phase 2 已覆盖 95%+ 场景，Phase 3 边际收益极低

### 12.2 为什么用 Ollama 作为默认 SLM Provider

- 局域网部署 (`http://120.24.117.59:11434`)，零公网依赖
- 模型切换灵活 (`glm-4.7-flash:latest` 默认)
- 通过 `ProviderManager` 统一接入，未来可扩展为其他本地 provider

### 12.3 为什么 Embedding 用 background daemon thread

- 避免首次 `classify()` 阻塞事件循环
- centroids 预计算只需一次，后续查询为纯 CPU cosine
- 失败时优雅降级（返回 None → Regex fallback）
