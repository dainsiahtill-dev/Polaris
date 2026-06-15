import { useMemo } from 'react';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { LogEntry } from '@/types/log';

function toNum(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

/**
 * Hook deriving LLM usage statistics from the live WebSocket runtime stream.
 *
 * Source of truth = Polaris's existing realtime framework, NOT file polling:
 *   emit_llm_event → MessageBus → WS /v2/ws/runtime → useRuntime.llmStreamEvents.
 * The journal `llm` channel (CanonicalLogEventV2) carries real per-call usage in
 * raw.data (prompt/completion tokens), which parseLlmStreamLine surfaces into
 * LogEntry.meta (promptTokens / completionTokens / totalTokens). We aggregate those
 * push-delivered events — no polling, no file read.
 *
 * Previously this polled `runtime/events/llm.observations.jsonl` — a phantom file no
 * backend code path writes — so the global token HUD was permanently empty in real
 * runs. This rewire roots out that defect by consuming the same realtime stream the
 * rest of the app already renders live.
 */
export function useUsageStats(llmStreamEvents: readonly LogEntry[] | null | undefined) {
  const stats = useMemo<UsageStats | null>(() => {
    const events = Array.isArray(llmStreamEvents) ? llmStreamEvents : [];
    if (events.length === 0) return null;

    const totals = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
    const byMode: Record<string, { total_tokens: number; calls: number }> = {};
    let calls = 0;

    for (const entry of events) {
      const meta = entry && typeof entry.meta === 'object' && entry.meta ? (entry.meta as Record<string, unknown>) : {};
      const streamEvent = String(meta['streamEvent'] || '').toLowerCase();
      // A discrete call = a "completed" lifecycle event. Recognise both the canonical
      // journal vocabulary (llm_completed/llm_failed) and the legacy one (invoke_*).
      const isCall =
        streamEvent === 'llm_completed' ||
        streamEvent === 'llm_failed' ||
        streamEvent === 'invoke_done' ||
        streamEvent === 'invoke_error';

      const prompt = toNum(meta['promptTokens']);
      const completion = toNum(meta['completionTokens']);
      const total = toNum(meta['totalTokens']) || prompt + completion;

      totals.prompt_tokens += prompt;
      totals.completion_tokens += completion;
      totals.total_tokens += total;

      if (isCall) {
        calls += 1;
        const mode = String(meta['role'] || entry.source || 'unknown').toLowerCase();
        if (!byMode[mode]) byMode[mode] = { total_tokens: 0, calls: 0 };
        byMode[mode].calls += 1;
        byMode[mode].total_tokens += total;
      }
    }

    if (calls === 0 && totals.total_tokens === 0) return null;

    return {
      totals,
      calls,
      // Journal usage is a real token count, never a char estimate.
      estimated_calls: 0,
      by_mode: byMode,
    };
  }, [llmStreamEvents]);

  return {
    stats,
    loading: false,
    error: null as string | null,
    // Push-based: usage flows from the WebSocket runtime stream; nothing to re-fetch.
    refresh: () => {},
  };
}
