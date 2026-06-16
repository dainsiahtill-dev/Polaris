# Blueprint — CCR Reversible Retrieve（可逆按需取回）T1-A

状态：Blueprint（先于实现）。日期：2026-06-16。
作者角色：Expert-R（Reversible Retrieve / CCR loop-close）。
来源裁决：`docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §T1-A。
约束：`src/backend/AGENTS.md` §4.1（非平凡后端先 Blueprint）；CLAUDE.md §7（防重复造轮子）、§8（禁业务代码）；UTF-8；mypy 干净；fail-closed。

---

## 1. 问题（已代码锚定）

压缩是**单向**的。我们会把工具输出 / 大块上下文「指针化」并注入引用标记，却**没有任何模型可调用的工具把原文取回**：

- 注入点 1：`polaris/kernelone/context/projection_engine.py:255` →
  `refs_text = "\n".join(f"[receipt_ref:{ref}]" for ref in safe_refs)`（受 DO-NOT-TOUCH 约束，不改）。
- 注入点 2：`polaris/kernelone/context/context_os/models_v2.py:627` 与 `models.py:1128` →
  `d["content"] = f"<receipt_ref:{ref_id}>"`。
- 引擎指针化：`polaris/kernelone/context/engine/engine.py:286 _pointerize_items` →
  `pointer = f"[See {path}]"`，以及 `_summarize_items` 的 `...[snip]...`（有损，不可逆）。

`SessionReceiptStore.get_receipt(job_id=...)`（`polaris/infrastructure/db/repositories/accel_session_receipt_store.py:684`）
只返回 receipt **行元数据**（`result_ref` 指针字符串 / `status` / `changed_files` / `tool` / `error_*`），
**不保存原始 payload**。即：现有 infra **无法**把一个被指针化掉的 payload 还原成原始字节。

结论（诚实）：HEADROOM 文档里「originals are not stored」分支成立 → 必须新增一个
**TTL 受限的原文缓存（CCR 风格）** 作为可逆存储，按内容哈希寻址；receipt-id 维度只能做
best-effort 元数据查询（拿回 `result_ref` 指针与状态），不能凭空还原原文。

---

## 2. 目标

新增一个**通用、平台级、模型可调用**的取回工具，闭合可逆压缩环：

1. 新工具 `context_retrieve(ref)`：输入一个引用（CCR 内容哈希标记 / receipt id / 裸标记），
   返回其原始 payload（若在 CCR 缓存命中）或 receipt 元数据（best-effort）。
2. 一个可逆的 CCR 原文缓存：`key = blake3(content)[:24]`，标记形如 `<<ref:HASH>>`，
   默认 TTL≈300s，读时惰性清过期；进程内内存为主、可选 sqlite 落盘。
3. 复用既有 infra：优先 `SessionReceiptStore`（拿 receipt 元数据）+ 既有指针标记格式对齐，
   不新建并行 receipt 存储。
4. 弱模型别名容忍（`ref`/`hash`/`id`/`pointer`/`receipt_ref`）走既有归一化层
   （`arg_aliases` SSOT），不用 teaching error。

---

## 3. 架构与数据流

```
                       projection / engine / context_os
                         |  注入标记（已存在，不改）
                         v
  [receipt_ref:RID]   <receipt_ref:RID>   <<ref:HASH>>   [See path]
                         |
            LLM 在下一回合主动调用 context_retrieve(ref=...)
                         |
                         v
   RoleToolGateway.execute_tool → AgentAccelToolExecutor.execute
     - canonicalize_tool_name("context_retrieve")
     - normalize_tool_arguments → SchemaDrivenNormalizer 用 arg_aliases 把 hash/id/... → ref
     - ToolSpecRegistry.get_all_specs()[context_retrieve] 校验 + _drop_unknown_arguments
     - handler = ToolHandlerRegistry.load_all()["context_retrieve"]
                         |
                         v
   handlers/context_retrieve.py:_handle_context_retrieve(executor, ref=...)
     1) 解包标记：剥离 <<ref:...>> / [receipt_ref:...] / <receipt_ref:...> 包裹，拿到裸 ref
     2) 若像 CCR 内容哈希 → OriginalPayloadCache.get(hash)  ── 命中=还原原文（可逆）
     3) 否则当 receipt job_id → SessionReceiptStore.get_receipt ── best-effort 元数据
     4) 都未命中 → ok=False + 明确 not_retrievable 原因（fail-closed，绝不静默成功）
```

### 3.1 模块职责

| 模块 | 路径（owned） | 职责 |
|------|------|------|
| 工具规格 | `tool_spec_registry.py` `_BUILTIN_REGISTRY`（**跨文件，需 orchestrator 落**） | 声明 `context_retrieve` 规格 + `arg_aliases` |
| 工具定义（角色 prompt/schema） | `definitions.py`（owned） | `create_default_registry` 透传 SSOT；可选确定性排序 pass |
| 执行 handler | `executor/handlers/context_retrieve.py`（NEW, owned） | 解析 ref → 原文/元数据，fail-closed |
| handler 注册 | `executor/handlers/registry.py`（owned） | `load_all()` 新增 context_retrieve 模块 |
| 可逆缓存 | `kernelone/llm/toolkit/original_payload_cache.py`（NEW, owned） | blake3 keyed、TTL、惰性清理、内存 + 可选 sqlite |
| 测试 | `executor/handlers/tests/`、`toolkit/tests/`（NEW, owned） | 正常/边界/异常/回归 |

### 3.2 OriginalPayloadCache 设计（CCR 风格，通用）

- `put(content: str) -> str`：返回 `<<ref:HASH>>` 标记，`HASH = blake3(content.encode("utf-8")).hexdigest()[:24]`。
- `get(ref_or_marker: str) -> str | None`：剥标记 → 查内存（未过期）→ 查 sqlite（可选）→ 提升回内存；过期惰性删除返回 None。
- TTL：默认 300s，`time.monotonic()` 计时；每次 `get`/`put` 触发一次轻量 lazy purge。
- 线程安全：`threading.RLock`。
- sqlite（可选、opt-in）：`runtime/cache/ccr_originals.db`（`resolve_runtime_path(workspace, "cache/...")`），
  纯内容寻址（content-addressed），无业务字段 → §8 安全。
- 纯通用容器：不含任何项目名 / 文件模板 / 域模型。

### 3.3 标记解析（统一）

`_strip_ref_markers(raw) -> str`：依次剥离 `<<ref:...>>`、`<<ccr:...>>`、`[receipt_ref:...]`、
`<receipt_ref:...>`、`[See ...]` 包裹，trim 后得到裸 ref。容忍弱模型把整条标记粘进 `ref` 参数。

---

## 4. 关键技术理由

1. **为什么不复用 receipt 存原文**：`get_receipt` 不存原文（已锚定）；`result_ref` 是指针不是内容。
   强行用它「还原」会是假信号（false-green）。CCR 缓存是唯一真能还原的反向通道。
2. **为什么 spec 必须进 `_BUILTIN_REGISTRY`**：`execute()`（core.py:311）只认 `ToolSpecRegistry.get_all_specs()`；
   `SchemaDrivenNormalizer` 单例缓存 `get_all_specs()`。`arg_aliases` 与 `_drop_unknown_arguments` 都依赖
   spec 存在，否则别名被丢、或报 Unknown tool。该文件不在本专家 owned 范围 → 作为**跨文件依赖上报**，
   由 orchestrator 落入；handler 内仍做**内联别名兜底**（防御性），双保险。
3. **为什么走既有归一化层而非 teaching error**：遵循 [[normalize-toolcalls-adapt-to-llm]]；`arg_aliases`
   是 SSOT 驱动，零额外代码即覆盖 `hash`/`id`/`pointer`/`receipt_ref` → `ref`。
4. **fail-closed**：任何解析失败 / 未命中 / 过期都返回 `ok=False` 带明确原因，绝不静默成功（CLAUDE.md 强约束）。
5. **§8**：缓存是内容寻址的纯通用容器，工具是平台能力，无任何目标项目逻辑。

---

## 5. 跨文件依赖（上报，不自行编辑）

- `polaris/kernelone/tool_execution/tool_spec_registry.py` `_BUILTIN_REGISTRY`：需新增 `context_retrieve` 规格。
  本专家不拥有该文件 → 在结构化输出 `shared_file_conflicts` 上报；若 orchestrator 授权，
  规格内容如下（待落）：
  ```python
  "context_retrieve": {
      "category": "read",
      "description": "Retrieve the ORIGINAL content behind a compression/receipt pointer "
                     "(e.g. <<ref:HASH>>, [receipt_ref:ID], <receipt_ref:ID>). Use this to "
                     "recover context that was pointerized away.",
      "aliases": ["expand_pointer", "expand_receipt", "fetch_receipt", "retrieve_original"],
      "arg_aliases": {"hash": "ref", "id": "ref", "pointer": "ref", "receipt_ref": "ref"},
      "arguments": [{"name": "ref", "type": "string", "required": True}],
      "response_format_hint": "Original payload (if cached) or receipt metadata (best-effort).",
      "required_any": [("ref",)],
      "required_doc": "args.ref required",
      "handler_module": "polaris.kernelone.llm.toolkit.executor.handlers.context_retrieve",
      "handler_function": "_handle_context_retrieve",
  },
  ```
  若 orchestrator 不授权改该文件，handler 提供 `ensure_context_retrieve_spec_registered()`，
  在 handler 模块 import 时 `ToolSpecRegistry.register("context_retrieve", spec)`（幂等、非 strict），
  作为 owned-only 的退路（代价：依赖 import 时序，故首选 `_BUILTIN_REGISTRY`）。

---

## 6. 验证

- `ruff check <files> --fix && ruff format <files>`
- `mypy <files>` → Success: no issues found
- `pytest`：缓存 put/get/TTL 过期/惰性清理/sqlite roundtrip；handler 命中 CCR、命中 receipt 元数据、
  未命中 fail-closed、标记剥离、别名 `hash`/`id`/`pointer` 经归一化层到 `ref`。
- 不跑全量套件（已知无关 pre-existing 失败 `test_transcript_leak_guard::...skips_compression`）。

## 7. 风险与边界

- 弱模型可能不主动调 retrieve（文档已注明）：本任务只提供「能力」；自动展开是后续 T1-A 第二半（非本专家）。
- spec 进 `_BUILTIN_REGISTRY` 是跨文件依赖；未落则 native 别名经 `_drop_unknown_arguments` 被丢，
  handler 内联兜底仍能从 `ref` 工作（退化但不致命）。
- sqlite 落盘默认关闭（opt-in），避免给纯读路径加 IO 副作用。
