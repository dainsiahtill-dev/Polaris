import { describe, expect, it } from 'vitest';

import { resolveModelName } from '../modelResolver';

describe('resolveModelName', () => {
  it('uses role model when role provider matches selected provider', () => {
    const result = resolveModelName({
      roleId: 'architect',
      providerId: 'minimax',
      llmConfig: {
        roles: {
          architect: {
            provider_id: 'minimax',
            model: 'MiniMax-M2.1',
          },
        },
        providers: {
          minimax: {
            model: 'abab6.5-chat',
          } as any,
        },
      },
    });

    expect(result.model).toBe('MiniMax-M2.1');
    expect(result.source).toBe('role_config');
  });

  it('falls back to provider model when role provider mismatches selected provider', () => {
    const result = resolveModelName({
      roleId: 'architect',
      providerId: 'minimax',
      llmConfig: {
        roles: {
          architect: {
            provider_id: 'ollama',
            model: 'modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest',
          },
        },
        providers: {
          minimax: {
            model: 'MiniMax-M2.1',
          } as any,
        },
      },
    });

    expect(result.model).toBe('MiniMax-M2.1');
    expect(result.source).toBe('provider_config');
  });

  it('keeps compatibility for legacy docs role key', () => {
    const result = resolveModelName({
      roleId: 'architect',
      providerId: 'minimax',
      llmConfig: {
        roles: {
          docs: {
            model: 'MiniMax-M2.1',
          },
        },
        providers: {
          minimax: {
            model: 'abab6.5-chat',
          } as any,
        },
      },
    });

    expect(result.model).toBe('MiniMax-M2.1');
    expect(result.source).toBe('role_config');
  });
});
