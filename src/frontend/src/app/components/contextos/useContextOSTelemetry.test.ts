import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { readFile } from '@/services/fileService';
import { useContextOSTelemetry } from './useContextOSTelemetry';

vi.mock('@/services/fileService', () => ({
  readFile: vi.fn(),
}));
const mockReadFile = vi.mocked(readFile);

function logWithTokens(total: number): string {
  return JSON.stringify({
    ts: '2026-06-15T10:00:00Z',
    ts_epoch: 1781856000,
    seq: 1,
    event_id: `e-${total}`,
    kind: 'observation',
    actor: 'PM',
    name: 'llm_call',
    refs: { mode: 'pm.planning' },
    ok: true,
    output: { usage: { prompt_tokens: total, completion_tokens: 0, total_tokens: total } },
  });
}

beforeEach(() => {
  mockReadFile.mockReset();
});

describe('useContextOSTelemetry', () => {
  it('reads and parses the observation log into telemetry', async () => {
    mockReadFile.mockResolvedValue({ ok: true, data: { content: logWithTokens(1500) } } as Awaited<
      ReturnType<typeof readFile>
    >);
    const { result } = renderHook(() => useContextOSTelemetry('/ws', { pollMs: 100_000 }));
    await waitFor(() => expect(result.current.telemetry.hasData).toBe(true));
    expect(result.current.telemetry.totalTokens).toBe(1500);
    expect(result.current.lastUpdated).not.toBeNull();
  });

  it('stays empty (no polling) when workspace is null', async () => {
    const { result } = renderHook(() => useContextOSTelemetry(null, { pollMs: 100_000 }));
    await act(async () => {});
    expect(result.current.telemetry.hasData).toBe(false);
    expect(mockReadFile).not.toHaveBeenCalled();
  });

  it('does not let a stale slow response clobber newer telemetry (generation guard)', async () => {
    // Two in-flight reads resolve out of order: the newer one resolves first
    // with 999, then the older one resolves late with 111. The runId guard must
    // keep 999 (the latest-requested data), not the stale 111.
    const resolvers: Array<(content: string) => void> = [];
    mockReadFile.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolvers.push((content) => resolve({ ok: true, data: { content } } as Awaited<ReturnType<typeof readFile>>));
        }) as ReturnType<typeof readFile>,
    );

    const { result, rerender } = renderHook(({ ws }) => useContextOSTelemetry(ws, { pollMs: 100_000 }), {
      initialProps: { ws: '/ws-A' },
    });
    // First fetch (workspace A) is in flight → resolvers[0].
    await waitFor(() => expect(resolvers.length).toBe(1));

    // Switch workspace → second fetch (workspace B) starts → resolvers[1].
    rerender({ ws: '/ws-B' });
    await waitFor(() => expect(resolvers.length).toBe(2));

    // Resolve the NEWER request first with 999.
    await act(async () => {
      resolvers[1](logWithTokens(999));
    });
    expect(result.current.telemetry.totalTokens).toBe(999);

    // Now the OLDER request resolves late with stale 111 — must be ignored.
    await act(async () => {
      resolvers[0](logWithTokens(111));
    });
    expect(result.current.telemetry.totalTokens).toBe(999);
  });
});
