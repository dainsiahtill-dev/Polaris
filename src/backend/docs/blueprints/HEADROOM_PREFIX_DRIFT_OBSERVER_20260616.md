# Blueprint — Headroom 前缀稳定性观测器（Prefix-Drift Observer，T1-B 第 1 步）

状态: Execution-ready（仅落 STEP 1 = 零风险纯观测；不改字节、不重排工具）
日期: 2026-06-16
负责人角色: Expert-D（Prefix-Drift Observer）
权威依据: `docs/research/HEADROOM_CROSS_POLLINATION_20260616.md` §1 T1-B、`src/backend/AGENTS.md §4.1`

---

## 0) 目标与非目标

**目标（仅此）**：为每个 session/run 装一个「前缀稳定性观测器」。给定网关装配出的
**cache-hot 前缀**（role system_prompt + 早期/冻结的 system 消息），计算一个**确定性
SHA-256 指纹**；在同一 session 的连续装配间，检测指纹是否变化（= 前缀漂移，会击穿本地
vLLM APC / llama.cpp prompt cache）。并把前缀里的**易变 token**（ISO-8601 时间戳、
UUIDv4、run_id 样式字段）标记为 warning。**只报告会击穿缓存的东西，绝不改请求字节。**

**非目标（明确排除，留给 T1-B 第 2 步 / T1-C，本次不做）**：
- ✗ 工具定义排序 / JSON Schema key 归一（`tool_def_normalize`）
- ✗ 把易变 token 移出前缀 / 重写任何消息内容
- ✗ 改 compaction / live-zone 触发策略（`compaction_strategy.py`、`intelligent_compressor.py`）
- ✗ 改投影顺序（`projection_engine.py`）

> 这是一个**纯诊断信号**。先用数据证明 Polaris 到底有没有前缀抖动，再决定要不要做归一重构。

---

## 1) 文本架构图

```
RoleContextGateway._build_context_impl  (gateway.py)
    ... 组装 messages ...
    system_prompt 前插（ADR-0090 I4.3，line ~627）
    ↓
    [已存在] self._emit_context_build_observation(...)        # context.build 观测
    ↓
    [新增]   self._emit_prefix_drift_observation(request,     # ← 本次新增的非变异 emit
                 messages=messages, system_prompt=system_prompt)
                 │
                 │  复用 resolve_run_dir + emit_event（与 _emit_context_build_observation 逐字一致的 fail-safe）
                 ▼
            polaris/kernelone/context/cache_stability/drift_detector.py   # ← 新模块（纯函数 + 会话级状态）
                 ├─ extract_prefix(messages, system_prompt) -> PrefixSlice  # 切出 cache-hot 前缀（leading system 段）
                 ├─ fingerprint_prefix(slice) -> sha256 hex                 # 确定性指纹
                 ├─ scan_volatile_tokens(text) -> list[VolatileFinding]     # ISO-8601 / UUIDv4 / run_id 样式
                 └─ PrefixDriftObserver.observe(session_key, slice) -> PrefixDriftReport
                          （模块级 session→last_fingerprint 存储，复刻 ProjectionEngine learning_key 跨 turn 状态模式）
                 ▼
            context.prefix_drift 观测事件 → runtime.events.jsonl
                 → ContextOS 看板 / RoleSignalPlane（见 [[contextos-projection-engine]]、[[contextos-dashboard-realtime]]）
```

---

## 2) 模块职责

### 2.1 新模块 `polaris/kernelone/context/cache_stability/drift_detector.py`

纯、确定性、fail-safe（任何内部错误都不向 turn 抛出）。无 I/O、无业务/项目专用逻辑（§8）。

| 符号 | 类型 | 职责 |
|---|---|---|
| `VolatileKind` | `Enum`(str) | `ISO8601_TIMESTAMP` / `UUIDV4` / `RUN_ID_LIKE` |
| `VolatileFinding` | frozen dataclass | `{kind, sample, count}` — 报告某易变 token 在前缀里出现了几次（不存原文以外的敏感信息，sample 截断） |
| `PrefixSlice` | frozen dataclass | `{text: str, message_count: int, segment_roles: tuple[str,...]}` — 被指纹化的 cache-hot 前缀 |
| `PrefixDriftReport` | frozen dataclass | `{fingerprint, drifted, first_seen, previous_fingerprint, volatile_findings, prefix_chars, prefix_message_count}` |
| `extract_prefix(messages, system_prompt)` | fn | 从前插的 system_prompt + leading **连续 system 段**切出 cache-hot 前缀（首个非 system 消息处停止）。纯字符串拼接，不反序列化、不改 messages |
| `fingerprint_prefix(slice)` | fn | `sha256(prefix_text.encode("utf-8")).hexdigest()`，确定性 |
| `scan_volatile_tokens(text)` | fn | 用预编译正则扫 ISO-8601 / UUIDv4 / run_id 样式；返回 findings（去重计数，sample 截断 ≤ 64 字符） |
| `PrefixDriftObserver` | class | 持有 `dict[str, str]`（session_key → last_fingerprint）；`observe(session_key, slice)` 比对并返回 `PrefixDriftReport`；`reset()` 供测试 |
| `get_prefix_drift_observer()` | fn | 返回模块级单例 observer（跨 turn 状态，复刻 ProjectionEngine 模块级 learning_key 模式） |

**fail-safe 契约**：`observe()` 与所有纯函数对畸形输入返回安全默认（空前缀 → `fingerprint=""`、
`drifted=False`、`first_seen=True`），**永不 raise**。会话状态用线程锁保护（与 `io_events._event_seq_lock` 同款）。

### 2.2 `cache_stability/__init__.py`

仅 re-export 上述公开符号 + 一个「防重复造轮子提示」docstring（指明这是唯一前缀稳定性观测实现，
归一/重排留给后续 step，禁止在此塞业务逻辑）。

### 2.3 `gateway.py`（仅 emit 接线，不动任何 compaction/budget 逻辑）

新增私有方法 `_emit_prefix_drift_observation(request, *, messages, system_prompt)`，紧挨现有
`_emit_context_build_observation`，**逐字复刻**它的 run_id/events_path 解析 + run_dir 存在性守卫
+ try/except 吞错。在 `_build_context_impl` 内、现有 `self._emit_context_build_observation(...)`
调用点之后追加一行调用。session_key = `f"{run_id}:{role_id}"`（run+role 唯一定位一个角色会话；
ContextRequest 无独立 session_id 字段，run_id+role 是稳定身份）。

---

## 3) 核心数据流

1. `_build_context_impl` 装配完 `messages`（含前插 system_prompt）。
2. 调用 `_emit_prefix_drift_observation`：
   - 解析 events_path（同 context.build：显式优先，否则 run_dir + 存在性守卫）；无则静默跳过。
   - `slice = extract_prefix(messages, system_prompt)` 切出 cache-hot 前缀。
   - `report = get_prefix_drift_observer().observe(session_key, slice)`：
     - 计算 `fingerprint`；与该 session 上次指纹比对 → `drifted`；
     - `volatile_findings = scan_volatile_tokens(slice.text)`。
   - `emit_event(..., name="context.prefix_drift", kind="observation", refs={run_id, role, task_id}, output={fingerprint, drifted, first_seen, previous_fingerprint, prefix_chars, prefix_message_count, volatile_findings:[{kind,sample,count}]})`。
3. 看板 / RoleSignalPlane 直接读 `runtime.events.jsonl` 的 `context.prefix_drift`（无专用端点，见 [[contextos-dashboard-realtime]]）。

**指纹只覆盖 cache-hot 前缀**（system_prompt + leading system 段），不含尾部 run_card / 当前
user turn —— 那些本就每 turn 变，不是「漂移」。这与 headroom drift_detector「只对 system+tools+
早期消息做 per-session 指纹」一致。工具定义不在网关 messages 内（在 provider/toolkit 层注入），
本 step 的前缀指纹覆盖网关可见的 cache-hot system 段；工具定义的稳定性观测留给后续 step。

---

## 4) 技术理由

- **为何值钱**：Director 主循环跑本地 vLLM(APC)/llama.cpp(prompt cache)，前缀命中=TTFT 降、
  吞吐升，正中 [[velocity-replay-harness]]「瓶颈是 Director」。先量化抖动再改。
- **为何零风险**：纯观测，不碰字节，emit 与已验证的 context.build 同款 fail-safe（run_dir 不存在
  即跳过、所有异常吞掉），observability 永不破坏 turn。
- **为何放 kernelone/context 而非 cells**：前缀稳定性是 KernelOne 底座的通用上下文能力，与具体角色
  无关；网关只做接线。符合 CLAUDE.md「先复用 KernelOne 底座」。
- **§8 合规**：纯通用平台能力——正则/哈希/计数，无任何项目名、文件模板、域模型、硬编码路径。

---

## 5) 风险与边界

| 风险 | 缓解 |
|---|---|
| emit 失败破坏 turn | try/except 吞 OSError/ValueError，run_dir 守卫，逐字复刻 context.build |
| 模块级 session 状态跨测试泄漏 | `PrefixDriftObserver.reset()` + 测试用独立实例；单例仅生产路径用 |
| 误标易变 token（伪阳） | 正则保守（UUIDv4 严格 8-4-4-4-12、ISO-8601 带 T/Z、run_id 样式需 `*-NNNNN`/含 uuid 段）；只 warning 不阻断 |
| 前缀过大导致指纹开销 | 只切 leading system 段（通常 1-2 条），sha256 对几 KB 文本是微秒级 |
| 误以为覆盖了工具定义 | 文档明确：本 step 只覆盖网关可见 cache-hot system 段；工具定义稳定性属后续 step |

**绝不做**：mutate request bytes、reorder tools、改 compaction/projection。

---

## 6) 测试计划（`context_gateway/tests/test_prefix_drift_emission.py` + `cache_stability/tests/test_drift_detector.py`）

drift_detector 单元：
- 同前缀两次 observe → 第二次 `drifted=False`、`first_seen=False`、指纹相等。
- 前缀变一字节 → 第二次 `drifted=True`、`previous_fingerprint` 为旧值。
- 不同 session_key 互不污染。
- volatile 扫描：注入 ISO-8601 / UUIDv4 / run_id → 命中对应 kind；纯静态前缀 → 空 findings。
- 畸形输入（空 messages、None content、非 dict）→ 不 raise，安全默认。
- 确定性：同输入多次 fingerprint 相等。

gateway emit 集成（mirror test_context_build_emission.py）：
- 显式 events_path → 落一条 `context.prefix_drift`，字段齐全。
- 自解析 + run_dir 存在 → 落盘；run_dir 缺失 → 不落幻影文件。
- 无 run_id → 不 emit。
- emit_event 抛 OSError → 不 raise（never break a turn）。
- 同 run 连续两次 build_context 同前缀 → 第二条 `drifted=False`。

门禁：`ruff check --fix`、`ruff format`、`mypy`（Success: no issues found）、`pytest -q`（本切片 100% 绿）。
