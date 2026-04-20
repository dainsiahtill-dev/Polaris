import { describe, expect, it } from 'vitest';
import { parseLlmConfigMessage } from './llmEventMetaParser';

describe('parseLlmConfigMessage', () => {
  it('parses provider/model/backend from canonical config message', () => {
    const parsed = parseLlmConfigMessage(
      'provider=minimax-1771264734939, model=MiniMax-M2.5, backend=generic'
    );
    expect(parsed.provider).toBe('minimax-1771264734939');
    expect(parsed.model).toBe('MiniMax-M2.5');
    expect(parsed.backend).toBe('generic');
    expect(parsed.providerType).toBe('minimax');
    expect(parsed.modelType).toBe('generic');
  });

  it('parses backend prefixes for model type', () => {
    const parsed = parseLlmConfigMessage(
      'provider=codex_cli-1771000000000, model=gpt-5-codex, backend=generic:openai_compat'
    );
    expect(parsed.modelType).toBe('generic');
    expect(parsed.providerType).toBe('codex');
  });

  it('returns empty fields for unstructured message', () => {
    const parsed = parseLlmConfigMessage('PM loop bootstrapped');
    expect(parsed.provider).toBe('');
    expect(parsed.model).toBe('');
    expect(parsed.backend).toBe('');
    expect(parsed.modelType).toBe('');
  });
});
