import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { LLMConfig } from '@/app/components/llm/types';

const apiFetchMock = vi.hoisted(() => vi.fn());

vi.mock('@/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { useTestStore } from './testStore';

const resetTestStore = () => {
  useTestStore.setState({
    reportDrawer: { open: false, data: null },
    testSuites: { connectivity: true, response: true, qualification: false },
    testLevel: 'quick',
    runAllBusy: false,
    llmTesting: {},
    llmError: null,
  });
};

describe('useTestStore LLM runtime payloads', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetTestStore();
    window.polaris = {
      ...(window.polaris || {}),
      secrets: {
        available: vi.fn(async () => ({ ok: true, available: true })),
        get: vi.fn(async (key: string) => ({
          ok: key === 'llm:minimax',
          value: key === 'llm:minimax' ? 'resolved-minimax-key' : null,
        })),
        set: vi.fn(async () => ({ ok: true })),
        remove: vi.fn(async () => ({ ok: true })),
      },
    };
  });

  it('resolves keychain environment overrides before running CLI provider tests', async () => {
    let requestBody: Record<string, unknown> | null = null;
    apiFetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === '/v2/llm/test' && init?.method === 'POST') {
        requestBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({ ok: true, final: { ready: true } }), { status: 200 });
      }
      throw new Error(`unexpected request: ${url}`);
    });

    const llmConfig: LLMConfig = {
      schema_version: 1,
      providers: {
        codex: {
          type: 'codex_cli',
          name: 'Codex CLI',
          model: 'gpt-5-codex',
          env: {
            CODEX_API_KEY: '${keychain:llm:minimax}',
            STATIC_FLAG: ' enabled ',
          },
        },
      },
      roles: {
        director: { provider_id: 'codex', model: 'gpt-5-codex' },
      },
    };

    const report = await useTestStore
      .getState()
      .runLlmTest('director', llmConfig, 'quick', ['connectivity'], false);

    expect(report).toEqual({ ok: true, final: { ready: true } });
    expect(window.polaris?.secrets?.get).toHaveBeenCalledWith('llm:minimax');
    expect(requestBody?.env_overrides).toEqual({
      CODEX_API_KEY: 'resolved-minimax-key',
      STATIC_FLAG: 'enabled',
    });
  });
});
