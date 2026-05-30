import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LLMSettingsTab } from './LLMSettingsTab';
import type { LLMStatus } from './types';

vi.mock('./ProviderRegistry', () => {
  const providerEntry = {
    info: {
      name: 'OpenAI Compatible',
      type: 'openai_compat',
      description: 'Compatible HTTP provider',
      version: '1.0.0',
      author: 'Polaris',
      documentation_url: '',
      supported_features: ['chat'],
      cost_class: 'METERED',
      provider_category: 'cloud',
      autonomous_file_access: false,
      requires_file_interfaces: false,
      model_listing_method: 'manual',
    },
    defaultConfig: {
      type: 'openai_compat',
      name: 'OpenAI Compatible',
      model: 'qwen3-max',
    },
    component: () => null,
  };

  return {
    useProviderRegistry: () => ({
      loading: false,
      error: null,
      providers: [providerEntry],
      getProviderInfo: (type: string) => (type === 'openai_compat' ? providerEntry.info : undefined),
      getProviderDefaultConfig: (type: string) => (type === 'openai_compat' ? providerEntry.defaultConfig : undefined),
      getProviderComponent: (type: string) => (type === 'openai_compat' ? providerEntry.component : undefined),
      requiresApiKey: () => true,
      supportsFeature: () => false,
      getCostClass: () => 'METERED',
      validateProviderConfig: vi.fn(),
    }),
  };
});

const noopAsync = vi.fn(async () => null);

describe('LLMSettingsTab diagnostics', () => {
  it('surfaces the exact blocked provider, model, reason, and tested target without truncation-only UI', async () => {
    const llmStatus: LLMStatus = {
      state: 'BLOCKED',
      required_ready_roles: ['pm', 'director'],
      blocked_roles: ['pm'],
      unsupported_roles: [],
      roles: {
        pm: {
          provider_id: 'qwen-main',
          model: 'qwen3-max-current-with-long-region-routing-label',
          ready: false,
          runtime_supported: true,
          readiness_issue: 'model_mismatch',
          tested_provider_id: 'qwen-main',
          tested_model: 'qwen3-max-previously-tested-model',
          tested_timestamp: '2026-05-29T19:30:00Z',
        },
      },
      providers: {
        'qwen-main': {
          ready: false,
          suites: {
            connectivity: { ok: false },
          },
        },
      },
    };

    render(
      <LLMSettingsTab
        llmConfig={{
          schema_version: 1,
          providers: {
            'qwen-main': {
              type: 'openai_compat',
              name: 'Qwen Production Beijing Token Plan Provider With Long Billing Alias',
              model: 'qwen3-max-current-with-long-region-routing-label',
            },
          },
          roles: {
            pm: {
              provider_id: 'qwen-main',
              model: 'qwen3-max-current-with-long-region-routing-label',
            },
          },
        }}
        llmStatus={llmStatus}
        llmLoading={false}
        llmSaving={false}
        llmError={null}
        onSaveConfig={vi.fn(async () => true)}
        onRunInterview={noopAsync}
        onRunConnectivityTest={noopAsync}
        onAskInteractiveInterview={noopAsync}
        onSaveInteractiveInterview={noopAsync}
      />
    );

    await waitFor(() => expect(screen.getByTestId('llm-readiness-diagnostics')).toBeInTheDocument());

    expect(screen.getByTestId('llm-readiness-summary')).toHaveTextContent('未通过深度测试: PM');
    expect(screen.getByTestId('llm-readiness-summary')).not.toHaveTextContent('PM(Qwen Production Beijing Token Plan Provider');
    expect(screen.getByTestId('llm-readiness-diagnostic-provider')).toHaveTextContent('Qwen Production Beijing Token Plan Provider With Long Billing Alias');
    expect(screen.getByTestId('llm-readiness-diagnostic-provider')).toHaveTextContent('qwen-main');
    expect(screen.getByTestId('llm-readiness-diagnostic-model')).toHaveTextContent('qwen3-max-current-with-long-region-routing-label');
    expect(screen.getByTestId('llm-readiness-diagnostic-reason')).toHaveTextContent('最近通过测试的模型不是当前绑定模型');
    expect(screen.getByTestId('llm-readiness-diagnostic-tested')).toHaveTextContent('qwen3-max-previously-tested-model');
    expect(screen.getByTestId('llm-readiness-diagnostic-provider')).toHaveClass('break-words');
    expect(screen.getByTestId('llm-readiness-diagnostic-model')).toHaveClass('break-words');
    expect(screen.getByTestId('llm-readiness-diagnostic-reason')).toHaveClass('break-words');
  });
});
