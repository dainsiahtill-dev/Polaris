import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { ProviderConfig } from '../types';
import { ProviderContextProvider } from '../state';
import { DefaultProviderSettings } from './DefaultProviderSettings';
import { ProviderCard } from './ProviderCard';

function ProviderCardHarness({ initialProvider }: { initialProvider: ProviderConfig }) {
  const [provider, setProvider] = useState<ProviderConfig>(initialProvider);

  return (
    <ProviderContextProvider>
      <ProviderCard
        providerId="ollama"
        provider={provider}
        providerInfo={{
          name: 'Ollama Provider',
          type: 'ollama',
          supported_features: [],
        }}
        ProviderComponent={DefaultProviderSettings}
        connectivityStatus="unknown"
        costClass="LOCAL"
        onUpdate={(_providerId, updates) => {
          setProvider((prev) => ({ ...prev, ...updates }));
        }}
        onDelete={() => {}}
        onTest={() => {}}
      />
    </ProviderContextProvider>
  );
}

describe('ProviderCard', () => {
  it('keeps name input editable when initial name is 0 and then cleared', () => {
    render(
      <ProviderCardHarness
        initialProvider={{
          type: 'ollama',
          name: 0 as unknown as string,
          timeout: 60,
        }}
      />
    );

    fireEvent.click(screen.getByTestId('provider-edit-button-ollama-provider'));

    const nameInput = screen.getByPlaceholderText('我的 LLM 提供商');
    expect(nameInput).toHaveValue('0');

    fireEvent.change(nameInput, { target: { value: '' } });
    expect(nameInput).toHaveValue('');
  });

  it('keeps dense provider status controls readable with long names and models', () => {
    const longProviderName = 'Qwen Production Provider With A Very Long Region And Billing Alias';
    const longModelName = 'qwen3-max-preview-with-extended-tool-contract-and-long-routing-suffix';

    const { container } = render(
      <ProviderCardHarness
        initialProvider={{
          type: 'ollama',
          name: longProviderName,
          model: longModelName,
          timeout: 60,
        }}
      />
    );

    const header = container.querySelector('[data-provider-id="ollama"] [data-testid^="provider-card-header-"]');
    const actions = container.querySelector('[data-provider-id="ollama"] [data-testid^="provider-card-actions-"]');

    expect(header).toHaveClass('min-w-0', 'flex-col', 'lg:flex-row');
    expect(actions).toHaveClass('shrink-0', 'flex-wrap');
    expect(screen.getByText(longModelName)).toHaveClass('truncate', 'sm:max-w-72');
  });
});
