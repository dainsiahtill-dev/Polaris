# Polaris Context Engine v2 升级计划

目标：把“正确骨架”的上下文管理升级为**可靠、可运营、可回放**的企业级系统，吸收主流 Agent 的关键能力，同时保持 Polaris 宪法约束（合同不可变、事件流追加、run_id 全局唯一、UI 只读、可回放、失败可定位、Atomic IO、Memory refs）。

---

## 0. 现状评估（正确骨架）

Polaris 已具备稳定基础：

- 无状态调用 + Host 端上下文管理
- 分层上下文：Persona / Memory / Reflection / Plan / Target
- 角色隔离 + run/step 绑定
- 本地存储 + Atomic IO（write_text_atomic）
- 事件流（events.jsonl）+ Memory refs 强制
- Pydantic schema 结构化数据

---

## 1. 核心升级目标

### A) 上下文选择更可解释、更可控
- 替换固定 top_k=10 为动态策略
- 每个选择都有明确 reason + refs
- 产出可审计的 ContextPack

### B) 压缩 / 缓存 / 重用工程化
- 避免重复计算与重建
- 预算梯度处理可解释
- 多层缓存提升性能

### C) 上下文构建过程可观测
- ContextPack 写入事件流
- Glass Mind 可展示构建决策
- 失败可定位、可回放

---

## 2. Phase 1：Context Engine 核心对象（1-2 天）

### 2.1 Schema 设计（Pydantic）

```python
# context_engine.py
class ContextRequest(BaseModel):
    run_id: str
    step: int
    role: str  # pm/director/qa/docs
    mode: str  # planning/execution/qa/review
    task_id: Optional[str]
    query: str  # 当前意图
    budget: ContextBudget
    sources_enabled: List[str]
    policy: Dict[str, Any]

class ContextBudget(BaseModel):
    max_tokens: int
    max_chars: int
    cost_class: str  # LOCAL/FIXED/METERED

class ContextItem(BaseModel):
    id: str
    kind: str  # docs/memory/reflection/evidence/artifact
    content_or_pointer: str
    refs: Dict[str, Any]
    size_est: int
    priority: int  # 1-10
    reason: str
    provider: str

class ContextPack(BaseModel):
    request_hash: str
    items: List[ContextItem]
    compression_log: List[Dict]
    rendered_prompt: str
    rendered_messages: List[Dict]
    total_tokens: int
    total_chars: int
    build_timestamp: datetime
```

### 2.2 ContextEngine 核心类

```python
class ContextEngine:
    def __init__(self, project_root: str):
        self.providers = self._init_providers()
        self.cache = ContextCache()

    def build_context(self, request: ContextRequest) -> ContextPack:
        # 1) 从 providers 收集候选 items
        # 2) 按角色策略排序/过滤
        # 3) 预算梯度处理
        # 4) 生成可审计的 ContextPack
        # 5) 写入 events.jsonl

    def _apply_budget_ladder(
        self, items: List[ContextItem], budget: ContextBudget
    ) -> Tuple[List[ContextItem], List[Dict]]:
        # 去重 → 裁剪 → 指针化 → 摘要 → 丢弃
```

---

## 3. Phase 2：Provider 插件化（2-3 天）

内部 Provider 类（不引入外部插件系统）：

```python
class BaseProvider(ABC):
    def collect_items(self, request: ContextRequest) -> List[ContextItem]: ...
    def estimate_size(self, item: ContextItem) -> int: ...

class DocsProvider(BaseProvider): ...
class ContractProvider(BaseProvider): ...
class RepoEvidenceProvider(BaseProvider): ...
class MemoryProvider(BaseProvider): ...
class EventsProvider(BaseProvider): ...
class RepoEvidenceProvider(BaseProvider): ...
```

### 3.1 角色策略固化

```python
ROLE_STRATEGIES = {
    "pm": {
        "max_items": 8,
        "required_providers": ["docs", "contract", "memory"],
        "forbidden_providers": ["repo_evidence"],
        "memory_limit": 3,
    },
    "director": {
        "max_items": 12,
        "required_providers": ["contract", "repo_evidence"],
        "memory_limit": 5,
        "evidence_required": True,
    },
    "qa": {
        "max_items": 10,
        "required_providers": ["contract", "events", "repo_evidence"],
        "focus_failures": True,
    }
}
```

> **不变量约束**：Memory/Reflection 必须带 refs；缺 refs 的条目只能作为 note 展示。

### 3.2 Repo/Events Provider 约定

- **RepoEvidenceProvider**：显式传入 file + line range（或 around + radius），生成可回放证据切片。
- **EventsProvider**：读取 `events.jsonl` 尾部片段作为 failure signature 与上下文证据。

---

## 4. Phase 3：预算梯度与压缩（2 天）

```python
def apply_budget_ladder(items: List[ContextItem], budget: ContextBudget):
    compression_log = []

    # 1. 去重（同一文件多段保留最高优先级）
    deduped = _deduplicate_by_file(items)
    if len(deduped) < len(items):
        compression_log.append({"action": "deduplicate"})

    # 2. 裁剪（slice 从 ±200 降到 ±60）
    if _estimate_tokens(deduped) > budget.max_tokens:
        trimmed = _trim_slices(deduped)
        compression_log.append({"action": "trim_slices", "details": "±200→±60"})

    # 3. 指针化（file + line + hash + 用途）
    if _estimate_tokens(trimmed) > budget.max_tokens:
        pointerized = _convert_to_pointers(trimmed)
        compression_log.append({"action": "pointerize"})

    # 4. 摘要替换（仅大块低频）
    if _estimate_tokens(pointerized) > budget.max_tokens:
        summarized = _summarize_large_blocks(pointerized)
        compression_log.append({"action": "summarize"})

    # 5. 丢弃低优先级（最后才丢）
    final = _drop_low_priority(summarized, budget)
    compression_log.append({"action": "drop_low_priority", "remaining": len(final)})

    return final, compression_log
```

---

## 5. Phase 4：事件流集成与观测（1-2 天）

### 5.1 ContextPack 事件记录

```python
def emit_context_events(pack: ContextPack, events_path: str):
    emit_event(events_path, "context.build", {
        "request_hash": pack.request_hash,
        "items_count": len(pack.items),
        "providers_used": sorted(set(i.provider for i in pack.items)),
        "total_tokens": pack.total_tokens,
        "compression_steps": len(pack.compression_log),
        "compression_details": pack.compression_log
    })

    for item in pack.items:
        emit_event(events_path, "context.item", {
            "item_id": item.id,
            "kind": item.kind,
            "provider": item.provider,
            "size_est": item.size_est,
            "priority": item.priority,
            "reason": item.reason,
            "refs": item.refs
        })
```

### 5.2 LLM 调用事件增强

```python
def emit_llm_events(events_path: str, pack: ContextPack, result: Dict):
    emit_event(events_path, "llm.invoke", {
        "context_hash": pack.request_hash,
        "provider": result["provider"],
        "model": result["model"],
        "latency_ms": result["latency_ms"],
        "usage": result["usage"],
        "tokens_in": pack.total_tokens,
        "tokens_out": result["usage"].get("total_tokens", 0)
    })
```

---

## 5.5 Phase 4.5：Context Snapshot（回放快照）

为避免“压缩导致回放漂移”，关键 LLM 调用前将 ContextPack 快照写入 artifacts：

- 路径：`.polaris/runtime/runs/<run_id>/evidence/context_snapshot_<hash>.json`
- 事件：`context.snapshot`（包含 `request_hash` / `snapshot_path` / `snapshot_hash`）
- 回放原则：**优先读取快照而非重新计算**

---

## 6. Phase 5：缓存层优化（1-2 天）

```python
class ContextCache:
    def __init__(self):
        self.repo_index_cache = {}
        self.summary_cache = SummaryCache()
        self.retrieval_cache = {}
        self.context_pack_cache = {}

    def get_cached_pack(self, request_hash: str) -> Optional[ContextPack]: ...
    def cache_pack(self, pack: ContextPack): ...
```

缓存策略：

- **Repo 索引缓存**：项目结构变更时失效
- **摘要缓存**：按 content_hash 长期复用
- **检索缓存**：query + repo_hash + topk 复用
- **ContextPack 缓存**：同 run/step 重试直接复用

---

## 7. Phase 6：Artifacts 文件化（1 天）

长输出写入 `.polaris/runtime/runs/<run_id>/evidence/*.txt`：

```python
def artifact_long_output(content: str, run_id: str, kind: str) -> str:
    artifact_dir = f".polaris/runtime/runs/{run_id}/evidence"
    os.makedirs(artifact_dir, exist_ok=True)

    content_hash = generate_hash(content)
    filename = f"{kind}_{content_hash[:8]}.txt"
    filepath = os.path.join(artifact_dir, filename)

    write_text_atomic(filepath, content)
    return filepath
```

ContextItem 使用指针化：

```python
item = ContextItem(
    kind="artifact",
    content_or_pointer=filepath,
    refs={"file_hash": content_hash, "original_size": len(content)},
    size_est=200,
    reason="Long tool output, see artifact file"
)
```

---

## 8. Phase 7：集成与测试（2-3 天）

### 8.1 渐进式迁移（向后兼容）

```python
def get_anthropomorphic_context_v2(project_root, role, query, step, run_id, phase):
    request = ContextRequest(
        run_id=run_id,
        step=step,
        role=role,
        mode=phase,
        query=query,
        budget=_get_budget_by_role(role),
        sources_enabled=["docs", "contract", "memory", "evidence"],
        policy=ROLE_STRATEGIES.get(role, {})
    )

    engine = ContextEngine(project_root)
    pack = engine.build_context(request)

    return {
        "persona_instruction": pack.rendered_prompt.split("##")[0],
        "anthropomorphic_context": pack.rendered_prompt,
        "prompt_context_obj": PromptContext(
            run_id=run_id,
            phase=phase,
            step=step,
            persona_id=f"{role}.v1",
            retrieved_mem_ids=[i.id for i in pack.items if i.kind == "memory"],
            retrieved_mem_scores=[],
            retrieved_ref_ids=[i.id for i in pack.items if i.kind == "reflection"],
            token_usage_estimate=pack.total_tokens
        ),
        "context_pack": pack
    }
```

### 8.2 测试策略

- 单元测试：每个 Provider 的 collect_items 逻辑
- 集成测试：ContextEngine 全流程
- 性能测试：缓存命中率与构建时间
- 回放测试：基于 events.jsonl 复建 ContextPack

---

## 8.5 Phase 7.5：Invariant Sentinel（自动合规检查）

将不变量“代码化”，每次 Loop 结束后自动检查：

- 合同不可变（goal/AC 未被修改）
- 事实流 append-only（events.jsonl 不回退）
- Memory refs（新增记忆具备证据引用）

产出事件：

- `invariant.check`：整体 PASS/FAIL
- `invariant.violation`：逐项违规细节

---

## 9. 实施优先级

### 高优先级（立即实施）
- ContextRequest / ContextPack schema
- ContextEngine 核心类 + DocsProvider
- 基础预算梯度处理
- 事件流集成

### 中优先级（1 周内）
- 其他 Providers 实现
- 角色策略固化
- 缓存层优化
- Artifacts 文件化
- Invariant Sentinel（自动合规检查）

### 低优先级（后续迭代）
- 高级摘要算法
- 向量检索优化
- UI 展示集成
- 性能监控与可视化

---

## 10. 风险与缓解

- **性能回归**：多层缓存 + 异步构建
- **复杂性增加**：保持向后兼容、渐进迁移
- **存储膨胀**：按 run_id 隔离 artifacts + 定期清理

---

## 11. 成功指标

**可靠性**
- ContextPack 构建成功率 > 99.9%
- 事件流完整性 100%
- Atomic IO 失败率 < 0.1%

**性能**
- ContextPack 缓存命中率 > 80%
- 构建时间 < 500ms（命中缓存）
- token 使用效率提升 20%

**可观测性**
- 每个 ContextPack 可回放
- 压缩决策 100% 可解释
- 失败根因定位 < 5 分钟
