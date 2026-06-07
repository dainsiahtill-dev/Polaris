import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { LLMConfig } from '@/app/components/llm/types';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { useLLMStore } from './llmStore';

const resetLlmStore = () => {
  useLLMStore.setState({
    llmConfig: null,
    llmStatus: null,
    llmLoading: false,
    llmSaving: false,
    llmError: null,
    providerModels: {},
    providerKeyDrafts: {},
    providerKeyStatus: {},
    deletingProviders: {},
  });
};

const readyStatus = {
  state: 'READY',
  required_ready_roles: [],
  blocked_roles: [],
  unsupported_roles: [],
  roles: {},
  providers: {},
};

describe('useLLMStore save pipeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetLlmStore();
    window.polaris = {
      ...(window.polaris || {}),
      secrets: {
        available: vi.fn(async () => ({ ok: true, available: true })),
        get: vi.fn(async () => ({ ok: false, value: null })),
        set: vi.fn(async () => ({ ok: true })),
        remove: vi.fn(async () => ({ ok: true })),
      },
    };
  });

  it('sanitizes orphan role bindings before persisting through the save queue', async () => {
    const savedBodies: LLMConfig[] = [];
    apiFetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/v2/llm/config' && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as LLMConfig;
        savedBodies.push(body);
        return new Response(JSON.stringify(body), { status: 200 });
      }
      if (url === '/v2/llm/status') {
        return new Response(JSON.stringify(readyStatus), { status: 200 });
      }
      throw new Error(`unexpected request: ${url}`);
    });

    const firstConfig: LLMConfig = {
      schema_version: 1,
      providers: {
        keep: { type: 'openai_compat', name: 'Keep', model: 'keep-model' },
      },
      roles: {
        pm: { provider_id: 'deleted', model: 'deleted-model', profile: 'pm-default' },
      },
    };
    const latestConfig: LLMConfig = {
      schema_version: 1,
      providers: {
        keep: { type: 'openai_compat', name: 'Keep', model: 'keep-model' },
      },
      roles: {
        pm: { provider_id: 'deleted', model: 'deleted-model', profile: 'pm-default' },
        director: { provider_id: ' keep ', model: ' keep-model ', profile: 'director-default' },
      },
    };

    const firstSave = useLLMStore.getState().saveLLMConfig(firstConfig);
    const latestSave = useLLMStore.getState().saveLLMConfig(latestConfig);

    await expect(Promise.all([firstSave, latestSave])).resolves.toEqual([true, true]);

    expect(savedBodies).toHaveLength(1);
    expect(savedBodies[0]?.roles.pm).toEqual({ profile: 'pm-default' });
    expect(savedBodies[0]?.roles.director).toEqual({
      provider_id: 'keep',
      model: 'keep-model',
      profile: 'director-default',
    });
  });
});
