import { renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useContextOSTelemetry } from './useContextOSTelemetry';

const mockReadTail = vi.fn();
vi.mock('@/services/api', () => ({
  fileV2Service: {
    readTail: (...args: unknown[]) => mockReadTail(...args),
  },
}));

describe('useContextOSTelemetry', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockReadTail.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns empty telemetry when disabled', () => {
    const { result } = renderHook(() =>
      useContextOSTelemetry({ workspace: '.', enabled: false }),
    );
    expect(result.current.telemetry.hasData).toBe(false);
    expect(mockReadTail).not.toHaveBeenCalled();
  });

  it('polls journal file and parses events', async () => {
    mockReadTail.mockResolvedValue({
      ok: true,
      data: {
        content: JSON.stringify({
          event_id: 'evt-1',
          ts: new Date().toISOString(),
          actor: 'PM',
          channel: 'llm',
          message: 'llm_completed',
          meta: { promptTokens: 10, completionTokens: 5, totalTokens: 15, durationMs: 100 },
        }),
      },
    });

    const { result } = renderHook(() =>
      useContextOSTelemetry({ workspace: '.', runId: 'run-1', intervalMs: 4000, enabled: true }),
    );

    await waitFor(() => expect(result.current.telemetry.hasData).toBe(true));
    expect(result.current.telemetry.totalTokens).toBe(15);
    expect(result.current.telemetry.totalCalls).toBe(1);
  });
});
