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

import { interviewService, roleChatRolesService, roleChatService } from '../api';

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

  it('passes workspace through generic role-chat endpoints', async () => {
    const payload = { message: 'Review handoff', context: { taskId: 'PM-1' } };
    apiFetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, role: 'pm' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ready: true, workspace: 'C:/Temp/Product' }), { status: 200 }));

    await roleChatService.chat('pm', payload, 'C:/Temp/Product');
    await roleChatService.getStatus('pm', 'C:/Temp/Product');

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/v2/role/pm/chat?workspace=C%3A%2FTemp%2FProduct', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/v2/role/pm/chat/status?workspace=C%3A%2FTemp%2FProduct');
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

  it('does not expose the legacy SSE interview stream endpoint', () => {
    expect('stream' in interviewService).toBe(false);
  });
});
