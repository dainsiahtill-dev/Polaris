import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { OpenAIProviderSettings } from './OpenAIProviderSettings';
import type { ProviderConfig } from '../types';

const baseProvider: ProviderConfig = {
  type: 'openai_compat',
  name: 'OpenAI Compatible',
  base_url: 'https://example.com',
  api_path: '/v1/chat/completions',
  headers: {},
};

const validateOk = () => ({
  valid: true,
  errors: [],
  warnings: [],
});

describe('OpenAIProviderSettings', () => {
  it('updates headers from valid JSON input', () => {
    const onUpdate = vi.fn();
    render(
      <OpenAIProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('openai-custom-headers-input'), {
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
      <OpenAIProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('openai-custom-headers-input'), {
      target: { value: 'X-Test: abc\nx-api-version: 2023-06-01' },
    });

    expect(onUpdate).toHaveBeenCalledWith({
      headers: {
        'X-Test': 'abc',
        'x-api-version': '2023-06-01',
      },
    });
  });

  it('does not update headers for invalid input', () => {
    const onUpdate = vi.fn();
    render(
      <OpenAIProviderSettings provider={baseProvider} onUpdate={onUpdate} onValidate={validateOk} />
    );

    fireEvent.change(screen.getByTestId('openai-custom-headers-input'), {
      target: { value: '{"X-Test": "abc",' },
    });

    expect(onUpdate).not.toHaveBeenCalledWith(expect.objectContaining({ headers: expect.anything() }));
  });
});
