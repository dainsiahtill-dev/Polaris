# Headroom → Polaris：Compaction 硬化 + 类型感知确定性 Crusher（T2-A / T2-B）

> 范围：`src/backend/polaris/kernelone/context/`。
> 来源评估：`docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §T2-A / §T2-B。
> 本蓝图为 Expert-C（Compaction Hardening）落地依据，所有「现状」结论均经 codegraph 实读锚定。
> 约束：§8 禁业务代码、UTF-8、strict + 完整类型注解 + mypy clean、fail-closed、复用优先不造轮子。

---

## 0. 现状（codegraph 锚定）

| 符号 | 文件:行 | 角色 |
|------|---------|------|
| `CompactionStrategy.compact` | `compaction_strategy.py:158` | 计算 `original_tokens` / `final_tokens` / `tokens_recovered`，目前 **总是** 以 `triggered=compacted_items>0` 返回 |
| `_estimate_history_tokens` | `compaction_strategy.py:269` | history 级 token 估算（与 canonical 同公式） |
| `estimate_tokens`（canonical） | `_token_estimator.py:6` | ContextOS 唯一 token 估算真相，公式 `ascii/4 + cjk*1.5` |
| `IntelligentCompressor.compress` | `intelligent_compressor.py:352` | 贪心选 + `_summarize_items`（LLM 摘要 + 确定性兜底）；`compression_ratio` 已 clamp 到 ≤1（line 422） |
| `IntelligentCompressor._summarize_items` | `intelligent_compressor.py:477` | LLM 摘要前 **无确定性类型感知前置 pass** —— T2-B 注入点 |
| `IntelligentCompressor._estimate_tokens` | `intelligent_compressor.py:466` | 委托 canonical `estimate_tokens` |
| `_smart_log_truncate`（复用参考） | `context_os/summarizers/extractive.py:330` | 关键行/头尾日志截断（非 token 校验、非确定性 crush；仅作灵感，不直接调用——跨 Cell internal） |
| `_simple_truncate`（复用参考） | `context_os/summarizers/structured.py:389` | 行数截断（同上） |

### 关键事实（决定边界）
1. `IntelligentCompressor` **无 live caller**（仅 test + `artifact_compression.py:77` 文档注释里提到「将来 wire」）。它是 ContextOS 的压缩引擎实现，但当前主要由测试驱动。T2-B 仍应在此落地：它是类型无关 `_summarize_items` 的**唯一**确定性前置点，符合 headroom「ContentRouter → 专用 crusher → 兜底 LLM」分层。
2. `compress()` 的 `compression_ratio` 已 clamp ≤ 1.0，但**输出字符串本身仍可能比原文大**（summary token 超过省下的 token 时，selected 里塞了 `_summary` dict）。T2-A「never EXPAND」要求在此加 best-effort-smallest 守卫。
3. `BudgetExceededError` 的真实 `raise` 点全部在我**不拥有**的文件：`context_os/models.py:815`、`context_os/models_v2.py:245`、`chunks/budget.py:205`、`context_os/pipeline/stages.py:880`、`llm/engine/_executor_base.py:538`、`tool_execution/runtime_executor.py`。这些属于 budget-plane / gateway / tool-exec，**Expert-C 不得编辑**。→ 见 §4「上报的共享文件约束」。

---

## 1. T2-A：token-shrink 否决门 + 优雅降级（never EXPAND）

### 1A（owned，`compaction_strategy.py`）—— 否决门
在 `compact()` 末尾、构造 `CompactionResult` 之前插入不变式：

```
若 compaction 实际跑过（compacted_items>0）但 tokens_recovered <= 0：
    判定为 no-op → triggered=False、compacted_items=0、tokens_recovered=0，
    summary 标注 "[no-op: compaction did not reduce tokens]"。
```

理由：重序列化 / 占位符替换在小输入上可能**膨胀**；调用方绝不能把「没变小的 pass」当胜利。`tokens_recovered` 已是 `max(0, original-final)`，故判据等价于 `final_tokens >= original_tokens`。fail-closed：宁可报「没压」也不谎报「压了」。

### 1B（owned，`intelligent_compressor.py`）—— 压缩器 never-EXPAND 守卫
在 `compress()` 返回前：若最终 `compressed_content` 的真实 token（`self._estimate_tokens`）`>= original_tokens`，则**降级到 best-effort 最小**：丢弃会撑大的 `_summary` 项、保留已选高分项；若仍 `>=`，则返回已选项的纯拼接（不含 summary）。绝不返回比原文更大的串。`compression_ratio` 仍 clamp ≤1（既有）。这是 headroom「reject-if-not-smaller」在 apply 端的镜像。

> 注：budget enforcement 抛 `BudgetExceededError` 的下游 degrade（「压不到预算就保最小、绝不扩」）必须在 budget-plane 文件内做 —— 不在 Expert-C 范围。见 §4。

---

## 2. T2-B：内容类型感知确定性 Crusher（新模块 `crushers/`）

### 2.1 目录与契约
新建 `polaris/kernelone/context/crushers/`：

- `__init__.py` —— 导出 `crush_by_type`、`CrushResult`、`CrushKind`、各 crusher。
- `base.py` —— `CrushResult`（frozen dataclass: `text/original_tokens/crushed_tokens/ratio/kind`）、`CrushKind`(StrEnum: json/log/diff/search/none)、共享常量 `MIN_CRUSH_BYTES=512`、`_tokens()`（委托 canonical `estimate_tokens`，**不复制公式**）、`_finalize(original, crushed, kind)` —— **tokenizer-validated**：仅当 `crushed_tokens < original_tokens` 才返回 crushed，否则原样返回（kind=none）。所有 crusher 走同一 `_finalize` → 单点保证 headroom「reject-if-not-smaller」。
- `router.py` —— `crush_by_type(text, content_type=None)`：
  1. `len(text.encode("utf-8")) < MIN_CRUSH_BYTES` → 直接 none（跳过小输入）。
  2. `content_type` 显式给定则用之；否则 `detect_content_type(text)` 启发式。
  3. 分派到对应 crusher；未知类型 → none（原样）。
- `json_crush.py` —— 保 schema/keys + 样本行 + 离群 + 计数。大数组 → 保留前 K + 后若干 + `{"_crushed":{"omitted":N,"total":M}}`；保留所有唯一 key 路径作为 schema 摘要。非法 JSON → none。
- `log_crush.py` —— 模板提取 / 折叠重复行：把数字/十六进制/UUID/时间戳归一成占位得到「模板」，对连续同模板行折叠成 `<line> … (xN identical)`；保留首/尾 + 含 error/fail/exception/traceback 的关键行。
- `diff_crush.py` —— 去噪：保留 `+`/`-` hunk 行与 `@@` 头，折叠超长上下文（` ` 前缀）块为 `… (N context lines)`，丢 `index `/`diff --git` 噪声尾。
- `search_crush.py` —— 去重：按归一化行去重，保留首次出现 + `(xN)` 计数。

### 2.2 检测启发式（`detect_content_type`，确定性、无 LLM）
顺序短路：
1. strip 后以 `{`/`[` 起且 `json.loads` 成功 → json。
2. 含 `diff --git` 或多处 `^@@ ` 或 `^[+-]` 行占比高 → diff。
3. 多行且「时间戳/级别(INFO|WARN|ERROR|DEBUG)」行占比高 → log。
4. 形如 `path:line:` 重复（ripgrep/grep 风格）→ search。
5. 否则 none。

### 2.3 wire（owned，`intelligent_compressor.py`）
在 `_summarize_items` 构建 `combined_content` 之后、调用 LLM 之前，对 `combined_content` 跑 `crush_by_type`（确定性前置 pass）。仅当 `result.kind != none`（即真的更小）才用 crushed 文本喂 LLM / 兜底；否则用原文。fail-closed：crusher 异常被吞为 none（原样），绝不抬升 token。

---

## 3. 测试（owned，`crushers/tests/` + `context/tests/`）
- `crushers/tests/test_crush_router.py`：检测命中、<512B 跳过、未知类型 none、UTF-8。
- `crushers/tests/test_json_crush.py` / `test_log_crush.py` / `test_diff_crush.py` / `test_search_crush.py`：各类型「确实更小」+「不可压时原样返回（reject-if-not-smaller）」+ 边界。
- `context/tests/test_compaction_strategy_noop_guard.py`：non-shrinking pass → `triggered=False`。
- `context/tests/test_intelligent_compressor_never_expand.py`：summary 撑大场景 → 输出 token ≤ original。

每个 crusher 不变式：`crushed_tokens < original_tokens` 才返回 crushed，等则 none。全部 tokenizer-validated。

---

## 4. 上报的共享文件约束（Expert-C 不编辑）
- `compaction.py`（`RoleContextCompressor` 真实 apply 路径）—— 不在 owned 列表。T2-A 的 strategy 层 no-op 已覆盖 `compact()`；compressor apply 端 degrade 在 `intelligent_compressor.py`（owned）落地。
- `context_os/models.py` / `models_v2.py` / `chunks/budget.py` / `pipeline/stages.py` —— BudgetExceededError 真实 raise 点（budget-plane / gateway）。「压不到预算就保最小、绝不扩」的 degrade 必须在这些文件做，属其它 expert / gateway-owner。Expert-C 在 owned 文件内实现「never EXPAND」语义并上报此约束。
- `gateway.py` —— 明确不碰。

---

## 5. Self-check 门禁
`ruff check --fix && ruff format`、`mypy`（Success: no issues found）、`pytest`（owned 切片 100% 绿）。
