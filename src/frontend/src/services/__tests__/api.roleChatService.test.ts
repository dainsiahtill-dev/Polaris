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

import { roleChatRolesService, roleChatService } from '../api';

describe('roleChatService', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('sends Chief Engineer chat through the generic role-chat endpoint', async () => {
    const payload = { message: 'Review handoff', context: { taskId: 'PM-1' } };
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          ok: true,
          response: 'Blueprint ready',
          role: 'chief_engineer',
        }),
        { status: 200 },
      ),
    );

    const result = await roleChatService.chat('chief_engineer', payload);

    expect(result.ok).toBe(true);
    expect(result.data?.role).toBe('chief_engineer');
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chief_engineer/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  });

  it('loads the backend role list response shape', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          roles: ['pm', 'architect', 'chief_engineer', 'director', 'qa'],
          count: 5,
        }),
        { status: 200 },
      ),
    );

    const result = await roleChatRolesService.list();

    expect(result.ok).toBe(true);
    expect(result.data?.roles).toEqual(['pm', 'architect', 'chief_engineer', 'director', 'qa']);
    expect(result.data?.count).toBe(5);
    expect(apiFetchMock).toHaveBeenCalledWith('/v2/role/chat/roles');
  });
});
