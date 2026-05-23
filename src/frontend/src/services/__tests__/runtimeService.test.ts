import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: apiFetchMock,
}));

import { runtimeService } from '../api';

describe('runtimeService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('clears dialogue logs through the v2 runtime clear endpoint', async () => {
    apiFetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }));

    const result = await runtimeService.clearDialogue();

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/runtime/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'dialogue' }),
    });
  });
});
