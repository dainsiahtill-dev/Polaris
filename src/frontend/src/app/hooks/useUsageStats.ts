import { useMemo } from 'react';
import type { UsageStats } from '@/app/components/UsageHUD';
import type { LogEntry } from '@/types/log';
import { buildTelemetryFromStream } from '@/app/components/contextos/contextOSTelemetry';

/**
 * Hook deriving LLM usage statistics from the live WebSocket runtime stream.
 *
 * Source of truth = Polaris's existing realtime framework, not file reads:
 *   emit_llm_event → MessageBus → WS /v2/ws/runtime → useRuntime.llmStreamEvents.
 * The journal `llm` channel (CanonicalLogEventV2) carries real per-call usage in
 * raw.data (prompt/completion tokens), which parseLlmStreamLine surfaces into
 * LogEntry.meta (promptTokens / completionTokens / totalTokens). We aggregate those
 * push-delivered events — no timer loop, no file read.
 *
 * Previously this read `runtime/events/llm.observations.jsonl` on a repeat loop — a phantom file no
 * backend code path writes — so the global token HUD was permanently empty in real
 * runs. This rewire roots out that defect by consuming the same realtime stream the
 * rest of the app already renders live.
 */
export function useUsageStats(llmStreamEvents: readonly LogEntry[] | null | undefined) {
  const stats = useMemo<UsageStats | null>(() => {
    const events = Array.isArray(llmStreamEvents) ? llmStreamEvents : [];
    if (events.length === 0) return null;

    const telemetry = buildTelemetryFromStream(events, [], []);
    if (!telemetry.hasData || (telemetry.totalCalls === 0 && telemetry.totalTokens === 0)) return null;
    const byMode = Object.fromEntries(
      Object.entries(telemetry.byRole).map(([role, aggregate]) => [
        role,
        { total_tokens: aggregate.totalTokens, calls: aggregate.calls },
      ]),
    );

    return {
      totals: {
        prompt_tokens: telemetry.promptTokens,
        completion_tokens: telemetry.completionTokens,
        total_tokens: telemetry.totalTokens,
        cached_tokens: telemetry.cachedTokens,
        cache_creation_tokens: telemetry.cacheCreationTokens,
        cache_read_tokens: telemetry.cacheReadTokens,
        tool_tokens: telemetry.toolTokens,
        reasoning_tokens: telemetry.reasoningTokens,
        audio_tokens: telemetry.audioTokens,
      },
      calls: telemetry.totalCalls,
      // Journal usage is a real token count, never a char estimate.
      estimated_calls: telemetry.estimatedCalls,
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
