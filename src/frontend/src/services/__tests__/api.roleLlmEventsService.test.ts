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

import { roleLlmEventsV2Service } from '../api';

describe('roleLlmEventsV2Service', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('passes workspace when reading role-scoped LLM events', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          role: 'pm',
          workspace: 'C:/Temp/Product',
          events: [],
          stats: { total: 0 },
        }),
        { status: 200 },
      ),
    );

    const result = await roleLlmEventsV2Service.getByRole('pm', {
      limit: 5,
      offset: 2,
      workspace: 'C:/Temp/Product',
    });

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/role/pm/llm-events?limit=5&offset=2&workspace=C%3A%2FTemp%2FProduct',
    );
  });

  it('passes role and workspace when reading all LLM events', async () => {
    apiFetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          workspace: 'C:/Temp/Product',
          events: [],
          count: 0,
        }),
        { status: 200 },
      ),
    );

    const result = await roleLlmEventsV2Service.getAll({
      role: 'director',
      limit: 10,
      workspace: 'C:/Temp/Product',
    });

    expect(result.ok).toBe(true);
    expect(apiFetchMock).toHaveBeenCalledWith(
      '/v2/role/llm-events?limit=10&role=director&workspace=C%3A%2FTemp%2FProduct',
    );
  });
});
