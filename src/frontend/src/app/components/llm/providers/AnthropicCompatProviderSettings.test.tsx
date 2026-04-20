import { fireEvent, render, screen } from '@testing-library/react';
import { AnthropicCompatProviderSettings } from './AnthropicCompatProviderSettings';
import type { ProviderConfig } from '../types';

const baseProvider: ProviderConfig = {
  type: 'anthropic_compat',
  name: 'Anthropic Compatible',
  base_url: 'https://api.anthropic.com/v1',
  api_path: '/v1/messages',
  headers: {},
};

const validateOk = () => ({
  valid: true,
  errors: [],
  warnings: [],
});

describe('AnthropicCompatProviderSettings', () => {
  it('updates headers from valid JSON input', () => {
    const onUpdate = vi.fn();
    render(
      <AnthropicCompatProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('anthropic-custom-headers-input'), {
      target: { value: '{"X-Test":"abc","x-num":1}' },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      headers: {
        'X-Test': 'abc',
        'x-num': '1',
      },
    });
  });

  it('updates headers from key-value line format', () => {
    const onUpdate = vi.fn();
    render(
      <AnthropicCompatProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('anthropic-custom-headers-input'), {
      target: { value: 'X-Test: abc\nanthropic-version: 2023-06-01' },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      headers: {
        'X-Test': 'abc',
        'anthropic-version': '2023-06-01',
      },
    });
  });

  it('does not update headers for invalid input', () => {
    const onUpdate = vi.fn();
    render(
      <AnthropicCompatProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('anthropic-custom-headers-input'), {
      target: { value: '{"X-Test": "abc",' },
    });

    expect(onUpdate).not.toHaveBeenCalledWith(expect.objectContaining({ headers: expect.anything() }));
  });
});
