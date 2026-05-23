import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useBackendHealthPing } from './useBackendHealthPing';

const healthV2CheckMock = vi.hoisted(() => vi.fn());

vi.mock('@/services/api', () => ({
  healthV2Service: {
    check: healthV2CheckMock,
  },
}));

describe('useBackendHealthPing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('checks /v2/health and records healthy evidence', async () => {
    healthV2CheckMock.mockResolvedValueOnce({
      ok: true,
      data: {
        ok: true,
        version: '0.1',
        timestamp: '2026-05-23T05:20:00Z',
        lancedb_ok: true,
      },
    });
    const { result } = renderHook(() => useBackendHealthPing());

    let ok = false;
    await act(async () => {
      ok = await result.current.ping();
    });

    expect(ok).toBe(true);
    expect(healthV2CheckMock).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe('healthy');
    expect(result.current.evidence).toContain('/v2/health');
    expect(result.current.evidence).toContain('version=0.1');
    expect(result.current.evidence).toContain('lancedb=ok');
    expect(result.current.checkedAt).toBe('2026-05-23T05:20:00Z');
  });

  it('records unhealthy evidence when the health route fails', async () => {
    healthV2CheckMock.mockResolvedValueOnce({
      ok: false,
      error: 'Backend unavailable',
    });
    const { result } = renderHook(() => useBackendHealthPing());

    let ok = true;
    await act(async () => {
      ok = await result.current.ping();
    });

    expect(ok).toBe(false);
    expect(result.current.status).toBe('unhealthy');
    expect(result.current.error).toBe('Backend unavailable');
    expect(result.current.evidence).toContain('/v2/health');
    expect(result.current.evidence).toContain('Backend unavailable');
  });
});
