# ContextOS + CognitiveRuntime 完整实现蓝图

**日期**: 2026-04-07
**状态**: 规划中
**优先级**: P0-P2
**执行分支**: feature/enhanced-logger

---

## 背景

经过10人专家团队深度审计，发现 ContextOS + CognitiveRuntime 系统存在以下关键问题：

1. **LLM Summarization 完全Stub** - 上下文压缩永远使用简单策略
2. **_receipt_matches_case 永远返回 True** - Benchmark质量评分无意义
3. **工具搜索返回空** - 无法进行语义工具查找
4. **Auth Context 全未实现** - 无法进行权限验证
5. **评估器自动通过系统** - 无法真实验证上下文管理质量
6. **Metrics硬编码0** - 无法监控运行时指标
7. **错误传播未实现** - 错误只能日志不能事件化
8. **Policy env覆盖不完整** - 部分feature flag无法运行时配置

---

## P0 关键修复（必须完成）

### P0-1: LLM Summarization 实现

**文件**: `polaris/kernelone/context/engine/engine.py`

**问题**: `_summarize_items_llm()` 方法构建了 `combined_content` 但立即删除，直接回退到确定性摘要。

**修复**:
```python
async def _summarize_items_llm(self, items: list[ContextItem], focus: str) -> str:
    """使用LLM生成上下文摘要。"""
    # 构建合并内容
    combined_content = "\n\n---\n\n".join(
        f"[{item.kind}] {item.content_or_pointer}" for item in items if item.content_or_pointer
    )
    # 调用LLM生成摘要
    summary = await self._llm_client.summarize(combined_content, focus=focus)
    return summary
```

**验证**: 单元测试 + 集成测试确认LLM实际被调用。

---

### P0-2: _receipt_matches_case 实现

**文件**: `polaris/kernelone/context/strategy_benchmark.py:382-388`

**问题**: 函数永远返回 True，注释承认"完整实现会检查实际工具调用参数"。

**修复**: 实现真正的证据路径匹配逻辑：
```python
def _receipt_matches_case(receipt_data: dict[str, Any], case: BenchmarkCase) -> bool:
    """检查receipt的证据路径是否匹配case的期望。"""
    if not case.expected_evidence_path:
        return True

    # 获取实际工具调用
    tool_calls = receipt_data.get("tool_calls", [])
    evidence_found = set()

    for call in tool_calls:
        args = call.get("arguments", {})
        for key, value in args.items():
            if isinstance(value, str) and any(
                expected in value for expected in case.expected_evidence_path
            ):
                evidence_found.add(key)

    # 所有期望路径都必须被覆盖
    return evidence_found >= set(case.expected_evidence_path)
```

---

### P0-3: 工具语义搜索实现

**文件**: `polaris/kernelone/single_agent/tools/registry.py:310-313`

**问题**: `search_tools()` 返回空列表带TODO注释。

**修复**:
```python
def search_tools(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """基于embedding的语义工具搜索。"""
    if not query or not query.strip():
        return []

    # 优先使用缓存的embedding
    query_embedding = self._get_or_compute_embedding(query)

    # 向量相似度搜索
    results = []
    for tool_id, tool_data in self._tools.items():
        tool_embedding = tool_data.get("embedding")
        if tool_embedding:
            similarity = self._cosine_similarity(query_embedding, tool_embedding)
            results.append((similarity, tool_id, tool_data))

    # 排序返回top N
    results.sort(key=lambda x: x[0], reverse=True)
    return [
        {"tool_id": tid, "data": data, "score": score}
        for score, tid, data in results[:limit]
    ]
```

---

## P1 高优先级修复

### P1-1: Auth Context 实现

**文件**: `polaris/kernelone/auth_context/__init__.py:58-85`

**问题**: 7个方法全部 `raise NotImplementedError`。

**修复**: 实现基于session的auth context：
```python
class AuthContext:
    def __init__(self, session_store: SessionStore):
        self._session_store = session_store
        self._current_user: dict[str, Any] | None = None

    def validate_session(self, session_id: str) -> bool:
        """验证session有效性。"""
        session = self._session_store.get(session_id)
        if not session:
            return False
        return session.get("expires_at", 0) > time.time()

    def get_current_user(self) -> dict[str, Any] | None:
        """获取当前用户信息。"""
        return self._current_user

    def check_permission(self, permission: str) -> bool:
        """检查当前用户是否有指定权限。"""
        if not self._current_user:
            return False
        user_roles = self._current_user.get("roles", [])
        return permission in self._get_role_permissions(user_roles)
```

---

### P1-2: 评估器自动通过系统修复

**文件**: `polaris/kernelone/context/context_os/evaluation.py`

**问题**: 缺失数据自动获得70-100%分数。

**修复**: 移除自动通过逻辑，改为：
- 缺失数据 = 0分（不回归，但不给予额外分数）
- 只有实际测量的行为才能获得分数
- 添加 `confidence` 字段表示测量置信度

---

### P1-3: MetricsCollector 真实指标

**文件**: `polaris/kernelone/context/context_os/metrics_collector.py:194-200`

**问题**: `receipt_write_failure_rate` 和 `sqlite_write_p95_ms` 硬编码为 0.0。

**修复**: 实现真实测量：
```python
def _collect_from_sqlite_store(self, store_path: str) -> CognitiveRuntimeMetrics:
    # 实际测量写入延迟
    write_times = []
    for _ in range(100):
        start = time.perf_counter()
        self._execute_write(store_path)
        write_times.append(time.perf_counter() - start)

    write_times.sort()
    sqlite_write_p95_ms = write_times[int(len(write_times) * 0.95)] * 1000

    # 实际测量失败率
    failures = sum(1 for w in write_times if w > 0.1)  # >100ms视为潜在失败
    receipt_write_failure_rate = failures / len(write_times)
```

---

### P1-4: 错误事件传播

**文件**: 多处

**问题**: TODO指出错误应通过ToolError事件传播，但当前只记录日志。

**修复**: 在 `turn_engine/engine.py` 和 `constitution_adaptor.py` 中实现：
```python
async def _execute_single_tool(self, tool_call: dict[str, Any]) -> ToolResult:
    try:
        result = await self._tool_executor.execute(tool_call)
        return result
    except ToolExecutionError as e:
        # 通过事件传播错误
        await self._event_bus.publish(ToolError(
            tool_name=tool_call["name"],
            error=str(e),
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))
        raise
```

---

## P2 中优先级修复

### P2-1: Policy 环境变量覆盖完成

**文件**: `polaris/kernelone/context/context_os/models.py`

**问题**: `from_env()` 只处理部分字段。

**修复**: 添加缺失字段的环境变量支持：
```python
env_overrides: dict[str, str] = {
    # ... 现有字段 ...
    "KERNELONE_CONTEXT_OS_MIN_RECENT_FLOOR": "min_recent_floor",
    "KERNELONE_CONTEXT_OS_PREVENT_SEAL_ON_PENDING": "prevent_seal_on_pending",
    "KERNELONE_CONTEXT_OS_MODEL_CONTEXT_WINDOW": "model_context_window",
}
```

---

### P2-2: CJK感知的Token估算

**文件**: `polaris/kernelone/context/budget_gate.py:245-257`

**问题**: `len(text) // 4` 对CJK文本不准确。

**修复**: 使用正确的token估算：
```python
def estimate_tokens_for_text(self, text: str) -> int:
    if not text:
        return 0

    # 使用实际的tokenizer
    estimator = self._get_token_estimator()
    if estimator:
        return estimator.estimate_text_tokens(text)

    # 回退：ASCII 4chars/token, CJK 1.5chars/token
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    cjk_chars = len(text) - ascii_chars
    return max(1, int(ascii_chars / 4) + int(cjk_chars * 1.5))
```

---

## 执行计划

### Phase 1: P0 关键修复 (预计 2 天)
- [ ] P0-1: LLM Summarization 实现
- [ ] P0-2: _receipt_matches_case 实现
- [ ] P0-3: 工具语义搜索实现

### Phase 2: P1 高优先级 (预计 3 天)
- [ ] P1-1: Auth Context 实现
- [ ] P1-2: 评估器自动通过系统修复
- [ ] P1-3: MetricsCollector 真实指标
- [ ] P1-4: 错误事件传播

### Phase 3: P2 中优先级 (预计 2 天)
- [ ] P2-1: Policy 环境变量覆盖完成
- [ ] P2-2: CJK感知的Token估算

---

## 验证标准

1. **所有pytest测试通过** - `pytest polaris/kernelone/context/tests/`
2. **Ruff检查无警告** - `ruff check polaris/kernelone/context/`
3. **Mypy无错误** - `mypy polaris/kernelone/context/`
4. **Benchmark评分有效** - `_receipt_matches_case` 必须返回有意义的True/False
5. **工具搜索返回相关结果** - 搜索必须返回非空且相关性 > 0.5

---

## 风险与依赖

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM Summarization 需要LLM Client | P0-1依赖LLM Client实现 | 先用mock测试 |
| Auth Context 需要SessionStore | P1-1依赖SessionStore注入 | 使用可选依赖 |
| Benchmark fixtures格式不确定 | P0-2依赖fixture结构 | 先分析现有fixture |

---

**文档版本**: 1.0
**下次审查**: 2026-04-10
