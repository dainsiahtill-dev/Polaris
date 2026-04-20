import { fireEvent, render, screen } from '@testing-library/react';
import { BaseProviderSettings } from './BaseProviderSettings';
import type { ProviderConfig } from '../types';

const validateOk = () => ({
  valid: true,
  errors: [],
  warnings: [],
});

describe('BaseProviderSettings', () => {
  it('updates max_context_tokens from context window input', () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: 'openai_compat',
      name: 'OpenAI Compat',
      base_url: 'https://api.example.com/v1',
    };

    render(
      <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('provider-max-context-tokens-input'), {
      target: { value: '200000' },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_context_tokens: 200000 });
  });

  it('updates max_output_tokens from output input', () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: 'anthropic_compat',
      name: 'Anthropic Compat',
      base_url: 'https://api.example.com/v1',
    };

    render(
      <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('provider-max-output-tokens-input'), {
      target: { value: '8192' },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_output_tokens: 8192 });
  });

  it('falls back to max_tokens when max_output_tokens is missing', () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: 'openai_compat',
      name: 'OpenAI Compat',
      base_url: 'https://api.example.com/v1',
      max_tokens: 2048,
    };

    render(
      <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    const input = screen.getByTestId('provider-max-output-tokens-input') as HTMLInputElement;
    expect(input.value).toBe('2048');
  });

  it('clears max_output_tokens when input is emptied', () => {
    const onUpdate = vi.fn();
    const provider: ProviderConfig = {
      type: 'kimi',
      name: 'Kimi',
      base_url: 'https://api.moonshot.cn/v1',
      max_output_tokens: 4096,
    };

    render(
      <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('provider-max-output-tokens-input'), {
      target: { value: '' },
    });

    expect(onUpdate).toHaveBeenCalledWith({ max_output_tokens: undefined });
  });
});
