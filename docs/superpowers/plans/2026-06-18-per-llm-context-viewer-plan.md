# Per-LLM-Call Full Context Viewer — Implementation Recommendation

**Date:** 2026-06-19
**Scope:** Backend + Frontend enhancement for ContextOS real-time view.
**Goal:** Enable users to inspect the exact, post-compression messages sent to any LLM call on demand, without bloating the WebSocket stream or repeating the F14 redaction regression.

---

## 1. Chosen Approach: **Option C (Hybrid Summary + Hash)**

**Rationale**: The `emit_llm_event` pipeline already carries token counts, message counts, compression flags, and `call_id` in the `data` dict. Adding `prompt_hash` and `context_snapshot_ref` is purely additive — no new event types, no bridge changes, no WS protocol changes. The frontend ContextOS panel shows summaries (token counts, model, duration, role); the hash enables a "View full prompt" action that fetches on demand. This avoids the F14 lesson (never stream raw execution payloads over observability paths) and preserves WS bandwidth. The `AIExecutor` already computes `input_sha256` and `effective_prompt_sha256` in `_record_final_request_receipt()` — these hashes can be emitted into the event stream. A lightweight disk-backed store at `runtime/contexts/` holds the post-compression `effective_chat_messages` for on-demand retrieval.

---

## 2. Backend Changes

### File A: `src/backend/polaris/kernelone/llm/engine/executor.py`

**What to change**: After `_record_final_request_receipt()` (line 421-434), write the post-compression `effective_chat_messages` to disk and inject the hash into `AIResponse.metadata`.

**Exact code addition** (after line 434, before line 436):

```python
        # ContextOS full-context viewer: store post-compression messages by hash
        context_store_hash: str | None = None
        if effective_chat_messages:
            context_store_hash = self._store_context_messages(
                workspace=self.workspace,
                messages=effective_chat_messages,
                trace_id=trace_id,
                call_id=request.context.get("call_id") if isinstance(request.context, dict) else None,
            )
        # Inject hash into request context so upstream emit_llm_event can include it
        if isinstance(request.context, dict) and context_store_hash:
            request.context["context_snapshot_ref"] = context_store_hash
```

**Add new method** (after `_non_empty_str`, around line 683):

```python
    @staticmethod
    def _store_context_messages(
        workspace: str,
        messages: list[Any],
        trace_id: str,
        call_id: str | None = None,
    ) -> str:
        """Store compressed chat messages to runtime/contexts/ by SHA-256 hash.

        Returns the 24-char truncated hash used as the reference key.
        """
        import os
        from polaris.kernelone.storage.layout import StorageLayout

        payload = {
            "schema_version": 1,
            "trace_id": trace_id,
            "call_id": call_id,
            "messages": messages,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        full_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        hash_key = full_hash[:24]

        layout = StorageLayout(workspace=workspace)
        # Shard to avoid directory explosion: runtime/contexts/ab/abcdef...
        shard = hash_key[:2]
        dir_path = layout.get_path("runtime", f"contexts/{shard}")
        os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, hash_key)

        # Atomic write-then-rename to avoid partial reads
        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, file_path)
        return hash_key
```

**Also modify `_record_final_request_receipt`** to include `context_snapshot_ref` in the receipt payload (around line 628, after `tool_schema_sha256`):

```python
                "context_snapshot_ref": self._non_empty_str(context.get("context_snapshot_ref")),
```

### File B: `src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker.py`

**What to change**: In `_emit_call_start_event` (line 492-516) and `_emit_call_end_event` (line 850-877), the `metadata` already flows through `_with_context_os_audit`. The `context_snapshot_ref` is now in `request.context` (set by executor). Ensure it propagates.

**No changes needed** — `metadata` already carries `prepared` context which includes `context_snapshot_ref` via `_with_context_os_audit`. The `emit_llm_event` call sites in `event_emitter.py` already pass `metadata` through.

**Verify**: In `_emit_call_end_event` (line 850-877), the `metadata` dict already includes `elapsed_ms`, `cached`, `source`, `compression_applied`, `turn_round`. The `context_snapshot_ref` will be present if executor set it. Confirm by checking `prepared.context_result` or `request.context` has the field.

**Optional enhancement** (line 864, in `_emit_call_end_event`): Add `prompt_hash` to metadata if available from `prepared` or `AIResponse`. The `AIResponse` currently does not carry `prompt_hash` back — we can add it.

**Add to `AIResponse` return** (around line 879-901, the `return LLMResponse(...)`):

```python
                    metadata=_with_context_os_audit(
                        {
                            "model": response_model_name,
                            "provider": response_provider,
                            "native_tool_calls_count": len(native_tool_calls),
                            "elapsed_ms": round(elapsed_ms, 2),
                            "run_id": run_id,
                            "workspace": self.workspace,
                            "attempt": attempt,
                            "turn_round": turn_round,
                            "context_tokens": int(prepared.context_result.token_estimate) if prepared.context_result else 0,
                            # NEW: carry the hash from executor through to event emission
                            "context_snapshot_ref": prepared.context_result.context_snapshot_ref if prepared.context_result else None,
                        },
                        prepared,
                    ),
```

**Note**: `prepared.context_result` is a `TurnEngineContextResult` dataclass. We need to verify it has a `context_snapshot_ref` field, or we read from `request.context` instead. Since executor sets it on `request.context`, read from there in invoker.

**Revised approach**: In `invoker.py` `call()` method, after `response = await executor.invoke(request)`, read `context_snapshot_ref` from `request.context` and include it in the `_emit_call_end_event` metadata:

```python
            # After executor.invoke returns (around line 810+)
            context_snapshot_ref = request.context.get("context_snapshot_ref") if isinstance(request.context, dict) else None
            # ... then in _emit_call_end_event metadata:
            metadata=_with_context_os_audit(
                {
                    "elapsed_ms": round(elapsed_ms, 2),
                    "cached": False,
                    "source": "llm",
                    "compression_applied": prepared.context_result.compression_applied if prepared.context_result else False,
                    "turn_round": turn_round,
                    "context_snapshot_ref": context_snapshot_ref,  # NEW
                },
                prepared,
            ),
```

### File C: `src/backend/polaris/cells/roles/kernel/internal/llm_caller/event_emitter.py`

**What to change**: Already passes `metadata` through to `emit_llm_event` transparently. No changes needed — the `data` dict is free-form.

**Confirm**: `emit_call_start_event` (line 118-186) puts `metadata` into `emit_llm_event(metadata=...)`. `emit_call_end_event` (line 188-277) does the same. Since `context_snapshot_ref` is in `metadata`, it will flow automatically.

### File D: `src/backend/polaris/kernelone/events/io_events.py`

**What to change**: No changes. `emit_llm_event` already accepts free-form `data` dict and passes it through to JSONL + MessageBus + realtime bridge. The `data` dict will now contain `context_snapshot_ref` and `prompt_hash` automatically.

---

## 3. Frontend Changes

### File A: `src/frontend/src/app/hooks/useRuntime.ts`

**What to change**: In `parseLlmStreamLine` (around line 542), extract `context_snapshot_ref` and `prompt_hash` from `eventData` and inject into `LogEntry.meta`.

**Exact addition** (after line 553, inside the `meta` object construction):

```typescript
    const dataContextSnapshotRef = eventData ? String(eventData.context_snapshot_ref || '').trim() : '';
    const dataPromptHash = eventData ? String(eventData.prompt_hash || '').trim() : '';
    const dataTurnId = eventData ? String(eventData.turn_id || '').trim() : '';

    // ... inside meta object:
    const meta: Record<string, unknown> = {
      channel,
      streamEvent: normalizedEvent || undefined,
      role: actor || undefined,
      model: modelName || undefined,
      runId: runScope || undefined,
      promptTokens: safePromptTokens > 0 ? safePromptTokens : undefined,
      completionTokens: safeCompletionTokens > 0 ? safeCompletionTokens : undefined,
      totalTokens: usageTotalTokens > 0 ? usageTotalTokens : undefined,
      contextTokens: safeContextTokens > 0 ? safeContextTokens : undefined,
      durationMs: dataDurationMs > 0 ? Math.round(dataDurationMs) : undefined,
      // NEW fields for context viewer
      contextSnapshotRef: dataContextSnapshotRef || undefined,
      promptHash: dataPromptHash || undefined,
      turnId: dataTurnId || undefined,
    };
```

### File B: `src/frontend/src/app/components/contextos/contextOSTelemetry.ts`

**What to change**: Add `contextSnapshotRef`, `promptHash`, `turnId` to `ContextOSEvent` interface and map them in `logEntryToEvent`.

**Interface addition** (after line 65, in `ContextOSEvent`):

```typescript
  /** SHA-256 reference to the stored full context (post-compression messages). */
  contextSnapshotRef: string | null;
  /** SHA-256 of the serialized prompt (for integrity/audit). */
  promptHash: string | null;
  /** Correlates with the turn transaction this call belongs to. */
  turnId: string | null;
```

**Mapping addition** (in `logEntryToEvent`, around line 325, after `contextHash` extraction):

```typescript
  const contextSnapshotRef = nonEmptyString(meta['contextSnapshotRef']) || nonEmptyString(meta['context_snapshot_ref']);
  const promptHash = nonEmptyString(meta['promptHash']) || nonEmptyString(meta['prompt_hash']);
  const turnId = nonEmptyString(meta['turnId']) || nonEmptyString(meta['turn_id']);
```

**Return object addition** (in the returned `ContextOSEvent` object, around line 387):

```typescript
    contextSnapshotRef: contextSnapshotRef || null,
    promptHash: promptHash || null,
    turnId: turnId || null,
```

### File C: `src/frontend/src/app/components/contextos/contextOSData.ts`

**What to change**: Add `latestCallId`, `latestTurnId`, `latestContextSnapshotRef` to `RoleInternalContext` so the UI can link to a detail view.

**Interface addition** (in `RoleInternalContext`, after line 92):

```typescript
  /** Reference to the most recent stored full context (click to fetch detail). */
  latestContextSnapshotRef: string | null;
  /** Most recent call ID for this role. */
  latestCallId: string | null;
  /** Most recent turn ID for this role. */
  latestTurnId: string | null;
```

**Build logic addition** (in `buildRoleInternalContext`, around line 698):

```typescript
    // Find the most recent call with a context snapshot ref
    const lastCallWithSnapshot = roleEvents.find((event) => event.contextSnapshotRef);
    const latestContextSnapshotRef = lastCallWithSnapshot ? lastCallWithSnapshot.contextSnapshotRef : null;
    const latestCallId = lastCallWithSnapshot ? (lastCallWithSnapshot as any).callId || null : null;
    const latestTurnId = lastCallWithSnapshot ? lastCallWithSnapshot.turnId : null;
```

**Return object addition** (in the returned `RoleInternalContext`, around line 724):

```typescript
      latestContextSnapshotRef,
      latestCallId,
      latestTurnId,
```

### File D: New frontend component (context viewer modal)

**Create**: `src/frontend/src/app/components/contextos/ContextViewerModal.tsx`

**Purpose**: A read-only modal that fetches and displays the full LLM context by `contextSnapshotRef`.

**Sketch**:

```typescript
import { useState, useCallback } from 'react';
import { useSettings } from '@/hooks';

interface ContextViewerModalProps {
  contextSnapshotRef: string | null;
  roleId: string;
  onClose: () => void;
}

export function ContextViewerModal({ contextSnapshotRef, roleId, onClose }: ContextViewerModalProps) {
  const [content, setContent] = useState<unknown | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { settings } = useSettings({ autoLoad: true });

  const fetchContext = useCallback(async () => {
    if (!contextSnapshotRef) return;
    setLoading(true);
    try {
      const baseUrl = settings?.baseUrl || 'http://127.0.0.1:49977';
      const res = await fetch(`${baseUrl}/v2/context/${contextSnapshotRef}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setContent(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [contextSnapshotRef, settings]);

  // ... render modal with JSON viewer, collapsible sections for messages/tools
}
```

---

## 4. Schema / Example

### Backend event payload (what now flows through WS)

```json
{
  "schema_version": 1,
  "ts": "2026-06-19T08:15:30.123456Z",
  "seq": 42,
  "event_id": "evt-abc123",
  "run_id": "run-xyz789",
  "iteration": 3,
  "role": "director",
  "source": "llm_caller",
  "event": "call_end",
  "data": {
    "call_id": "call-uuid-1234",
    "turn_id": "turn-5678",
    "model": "qwen3-30b",
    "provider": "local_qwen",
    "prompt_tokens": 15234,
    "completion_tokens": 4096,
    "context_tokens_after": 15234,
    "elapsed_ms": 8420,
    "tool_calls_count": 2,
    "compression_strategy": "importance_based",
    "context_snapshot_ref": "a1b2c3d4e5f6...",
    "prompt_hash": "f6e5d4c3b2a1...",
    "workspace": "/home/user/project"
  }
}
```

### Stored context file (at `runtime/contexts/a1/a1b2c3d4e5f6...`)

```json
{
  "schema_version": 1,
  "trace_id": "trace-abc",
  "call_id": "call-uuid-1234",
  "messages": [
    {"role": "system", "content": "You are a coding assistant..."},
    {"role": "user", "content": "Implement a login feature..."},
    {"role": "assistant", "content": "I'll help you implement..."}
  ],
  "stored_at": "2026-06-19T08:15:30.000000Z"
}
```

### HTTP API response (`GET /v2/context/a1b2c3d4e5f6...`)

```json
{
  "schema_version": 1,
  "hash": "a1b2c3d4e5f6...",
  "trace_id": "trace-abc",
  "call_id": "call-uuid-1234",
  "messages": [
    {"role": "system", "content": "You are a coding assistant..."},
    {"role": "user", "content": "Implement a login feature..."},
    {"role": "assistant", "content": "I'll help you implement..."}
  ],
  "stored_at": "2026-06-19T08:15:30.000000Z",
  "message_count": 3,
  "total_chars": 4520
}
```

---

## 5. Security / Performance Notes

| Concern | Mitigation |
|---------|-----------|
| **F14 redaction regression** | Never apply `safe_observability_payload` to execution payloads. The hash store writes raw `effective_chat_messages` (no redaction). The `emit_llm_event` only emits the hash (opaque), not the content. |
| **Secrets in prompts** | The full prompt may contain API keys, file contents, or sensitive instructions. The hash is opaque; the fetch endpoint requires auth. Do not log the full content at INFO level. |
| **WS bandwidth** | Only ~200 bytes added per event (hash strings). No full content over WS. |
| **Disk I/O** | Typical Director run: 50-100 calls × ~50-200KB = 5-20MB. Stored in `runtime/contexts/` (volatile/ramdisk). Acceptable. |
| **Memory pressure** | No in-memory store. Content is file-backed. |
| **Auth/Z** | `GET /v2/context/{hash}` requires `require_auth` (existing JWT/session). Workspace-scoped: hash must belong to caller's workspace. Start with workspace-scoped access (any role in workspace can read any context). |

---

## 6. Implementation Phases

### Phase 1 (MVP) — Backend hash emission + frontend meta wiring

1. **Backend**: Add `_store_context_messages` to `executor.py`, call it after compression, inject `context_snapshot_ref` into `request.context`.
2. **Backend**: Thread `context_snapshot_ref` through `invoker.py` metadata to `_emit_call_end_event`.
3. **Frontend**: Extract `contextSnapshotRef` in `parseLlmStreamLine`, add to `ContextOSEvent`, surface in `RoleInternalContext`.
4. **Frontend**: Add a "View Context" button on role cards that opens a simple JSON viewer modal (fetch from new endpoint).
5. **Backend**: Add `GET /v2/context/{hash}` router in `delivery/http/routers/` (new file or extend `history.py`).

**Validation**:
- `ruff check src/backend/polaris/kernelone/llm/engine/executor.py --fix`
- `mypy src/backend/polaris/kernelone/llm/engine/executor.py`
- `pytest src/backend/polaris/kernelone/llm/engine/tests/ -q`
- `npm run typecheck` (frontend)
- `npm run lint` (frontend)

### Phase 2 (Optional) — Enhanced viewer + retention

1. **Frontend**: Rich context viewer with collapsible sections (system prompt, user messages, tool definitions, projected context items).
2. **Backend**: Add `POST /v2/context/store` and `DELETE /v2/context/{hash}` for explicit lifecycle management.
3. **Backend**: TTL cleanup for `runtime/contexts/` — 7-day retention aligned with `STORAGE_RETENTION_RUNTIME_STATE`.
4. **Backend**: Cap disk storage at 100MB per workspace, evict oldest first.

---

## 7. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| **Disk growth unbounded** | Medium | High | Phase 2 adds TTL + size cap. Phase 1 relies on `runtime/` being volatile (ramdisk or 7-day cleanup). |
| **Hash collision** | Low | Medium | `sha256[:24]` (96 bits) with collision check in store. Full 64-char hash available in file content for verification. |
| **Two streams diverge** | Medium | Medium | Ensure `AIExecutor` puts the same `context_snapshot_ref` into both `request.context` (for event emission) and receipt sink. Single source of truth: the hash computed at store time. |
| **Frontend fetch latency** | Low | Low | On-demand fetch only when user clicks. 4s polling interval unaffected. Modal shows loading state. |
| **Auth gap — role-level ACL** | Low | Medium | Phase 1 uses workspace-scoped auth. Phase 2 can add role-level filtering if needed. |
| **Breaking existing tests** | Medium | Medium | Additive fields only. No existing schema changes. Update tests that assert exact `emit_llm_event` payload shape to expect new optional fields. |

---

## Summary of Files to Touch

| File | Change | Phase |
|------|--------|-------|
| `src/backend/polaris/kernelone/llm/engine/executor.py` | Add `_store_context_messages`, inject `context_snapshot_ref` into `request.context` and receipt | 1 |
| `src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker.py` | Thread `context_snapshot_ref` through `_emit_call_end_event` metadata | 1 |
| `src/backend/polaris/delivery/http/routers/context.py` (new) | `GET /v2/context/{hash}` endpoint with auth | 1 |
| `src/frontend/src/app/hooks/useRuntime.ts` | Extract `contextSnapshotRef`/`promptHash`/`turnId` in `parseLlmStreamLine` | 1 |
| `src/frontend/src/app/components/contextos/contextOSTelemetry.ts` | Add fields to `ContextOSEvent`, map in `logEntryToEvent` | 1 |
| `src/frontend/src/app/components/contextos/contextOSData.ts` | Surface `latestContextSnapshotRef` in `RoleInternalContext` | 1 |
| `src/frontend/src/app/components/contextos/ContextViewerModal.tsx` (new) | Read-only modal to fetch and display full context | 1 |
| `src/backend/polaris/kernelone/events/io_events.py` | No changes needed — free-form `data` dict already passes through | — |
| `src/backend/polaris/cells/roles/kernel/internal/llm_caller/event_emitter.py` | No changes needed — metadata already passes through | — |
