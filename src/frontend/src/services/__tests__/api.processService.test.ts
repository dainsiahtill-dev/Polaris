import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.fn();

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

vi.mock('@/app/utils/devLogger', () => ({
  devLogger: {
    warn: vi.fn(),
  },
}));

import { processService } from '../api';

describe('processService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('labels Director start fallback errors as Director failures', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));

    const result = await processService.startDirector();

    expect(result.ok).toBe(false);
    expect(result.error).toBe('Failed to start Director');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/start', { method: 'POST' });
  });

  it('labels Director stop fallback errors as Director failures', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(null, { status: 500 }));

    const result = await processService.stopDirector();

    expect(result.ok).toBe(false);
    expect(result.error).toBe('Failed to stop Director');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/director/stop', { method: 'POST' });
  });
});
