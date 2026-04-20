import { describe, expect, it } from 'vitest';

import { resolveProviderAwareRoleModel, resolveProviderConfiguredModel } from '../providerModelResolver';
import { isConnectivityKeyForProvider } from '../../state/connectivityStore';
import { extractProviderIdFromConnectivityKey } from '../../state/CanonicalProviderBridge';

describe('providerModelResolver', () => {
  it('resolves provider model with model/model_id/default priority', () => {
    expect(
      resolveProviderConfiguredModel({
        type: 'minimax' as any,
        model_id: 'MiniMax-M2.1',
      } as any)
    ).toBe('MiniMax-M2.1');

    expect(
      resolveProviderConfiguredModel({
        type: 'minimax' as any,
        model: 'MiniMax-M2.1-Latest',
        model_id: 'MiniMax-M2.1',
      } as any)
    ).toBe('MiniMax-M2.1-Latest');
  });

  it('does not reuse role model when provider mismatches', () => {
    const model = resolveProviderAwareRoleModel(
      {
        provider_id: 'ollama',
        model: 'modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest',
      },
      'minimax',
      {
        type: 'minimax' as any,
        model: 'MiniMax-M2.1',
      } as any
    );
    expect(model).toBe('MiniMax-M2.1');
  });
});

describe('connectivity key guards', () => {
  it('matches only exact provider segment', () => {
    expect(isConnectivityKeyForProvider('pm::minimax', 'minimax')).toBe(true);
    expect(isConnectivityKeyForProvider('pm::mini', 'minimax')).toBe(false);
  });

  it('extracts provider id from role::provider key', () => {
    expect(extractProviderIdFromConnectivityKey('pm::minimax')).toBe('minimax');
    expect(extractProviderIdFromConnectivityKey('minimax')).toBe('minimax');
  });
});
