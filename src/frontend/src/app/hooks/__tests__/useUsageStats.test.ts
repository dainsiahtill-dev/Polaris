import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';

import { useUsageStats } from '../useUsageStats';
import type { LogEntry } from '@/types/log';

/**
 * useUsageStats derives the global token HUD from the live WebSocket LLM stream
 * (journal `llm` channel; raw.data usage surfaced into LogEntry.meta by
 * parseLlmStreamLine) — no file polling, no phantom `llm.observations.jsonl`.
 */
function llmEvent(over: Partial<LogEntry> & { id: string }): LogEntry {
  return { timestamp: '2026-06-15T10:00:00Z', level: 'success', source: 'PM', message: '', ...over };
}

const PM_DONE = llmEvent({
  id: 'c1',
  source: 'PM',
  meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'PM', promptTokens: 1932, completionTokens: 1454, totalTokens: 3386 },
});
const DIRECTOR_DONE = llmEvent({
  id: 'c2',
  source: 'Director',
  meta: { channel: 'llm', streamEvent: 'llm_completed', role: 'Director', promptTokens: 800, completionTokens: 200, totalTokens: 1000 },
});
const WAITING = llmEvent({
  id: 'w1',
  level: 'thinking',
  meta: { channel: 'llm', streamEvent: 'llm_waiting', role: 'PM' },
});

describe('useUsageStats (WebSocket-derived)', () => {
  it('returns null when there are no stream events', () => {
    const { result } = renderHook(() => useUsageStats([]));
    expect(result.current.stats).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('returns null for null/undefined input (no crash)', () => {
    expect(renderHook(() => useUsageStats(null)).result.current.stats).toBeNull();
    expect(renderHook(() => useUsageStats(undefined)).result.current.stats).toBeNull();
  });

  it('aggregates real per-call tokens and calls from the journal llm stream', () => {
    const { result } = renderHook(() => useUsageStats([PM_DONE, DIRECTOR_DONE, WAITING]));
    const stats = result.current.stats;
    expect(stats).not.toBeNull();
    expect(stats?.totals.total_tokens).toBe(4386); // 3386 + 1000
    expect(stats?.totals.prompt_tokens).toBe(2732); // 1932 + 800
    expect(stats?.totals.completion_tokens).toBe(1654); // 1454 + 200
    // llm_waiting is not a discrete completed call → not counted.
    expect(stats?.calls).toBe(2);
    expect(stats?.estimated_calls).toBe(0);
  });

  it('buckets usage by role (by_mode)', () => {
    const { result } = renderHook(() => useUsageStats([PM_DONE, DIRECTOR_DONE]));
    const byMode = result.current.stats?.by_mode ?? {};
    expect(byMode['pm']).toEqual({ total_tokens: 3386, calls: 1 });
    expect(byMode['director']).toEqual({ total_tokens: 1000, calls: 1 });
  });

  it('still counts legacy invoke_done events (back-compat)', () => {
    const legacy = llmEvent({
      id: 'l1',
      source: 'PM',
      meta: { channel: 'llm', streamEvent: 'invoke_done', role: 'PM', totalTokens: 500 },
    });
    const { result } = renderHook(() => useUsageStats([legacy]));
    expect(result.current.stats?.calls).toBe(1);
    expect(result.current.stats?.totals.total_tokens).toBe(500);
  });

  it('uses the same ContextOS parser for cache/reasoning usage details', () => {
    const responseComplete = llmEvent({
      id: 'response-complete',
      source: 'Director',
      meta: {
        channel: 'llm',
        streamEvent: 'response.completed',
        role: 'Director',
        usage: {
          input_tokens: 100,
          cache_read_input_tokens: 50,
          output_tokens: 10,
          output_tokens_details: { reasoning_tokens: 7 },
        },
      },
    });
    const { result } = renderHook(() => useUsageStats([responseComplete]));
    expect(result.current.stats?.calls).toBe(1);
    expect(result.current.stats?.totals.prompt_tokens).toBe(150);
    expect(result.current.stats?.totals.completion_tokens).toBe(10);
    expect(result.current.stats?.totals.total_tokens).toBe(160);
    expect(result.current.stats?.totals.cached_tokens).toBe(50);
    expect(result.current.stats?.totals.reasoning_tokens).toBe(7);
  });

  it('returns null when events carry activity but no usage tokens', () => {
    const { result } = renderHook(() => useUsageStats([WAITING]));
    expect(result.current.stats).toBeNull();
  });
});
