import { describe, expect, it } from 'vitest';
import { sanitizeLlmConfigForSave } from '../configSanitizer';
import type { ProviderConfig, RoleConfig } from '../../types';

type TestLlmConfig = {
  providers?: Record<string, ProviderConfig>;
  roles?: Record<string, RoleConfig>;
  visual_layout?: Record<string, unknown>;
};

describe('sanitizeLlmConfigForSave', () => {
  it('removes provider and model bindings when a role points at a deleted provider', () => {
    const config: TestLlmConfig = {
      providers: {
        keep: { name: 'Keep Provider', model: 'keep-model' },
      },
      roles: {
        pm: { provider_id: 'deleted', model: 'deleted-model', profile: 'pm-default' },
        director: { provider_id: 'keep', model: 'keep-model', profile: 'director-default' },
      },
      visual_layout: { x: 1 },
    };

    const sanitized = sanitizeLlmConfigForSave(config);

    expect(sanitized).not.toBe(config);
    expect(sanitized.providers).toBe(config.providers);
    expect(sanitized.visual_layout).toBe(config.visual_layout);
    expect(sanitized.roles?.pm).toEqual({ profile: 'pm-default' });
    expect(sanitized.roles?.director).toEqual({
      provider_id: 'keep',
      model: 'keep-model',
      profile: 'director-default',
    });
  });

  it('trims existing provider and model bindings before saving', () => {
    const config: TestLlmConfig = {
      providers: {
        keep: { name: 'Keep Provider', model: 'keep-model' },
      },
      roles: {
        director: { provider_id: ' keep ', model: ' keep-model ', profile: 'director-default' },
      },
    };

    const sanitized = sanitizeLlmConfigForSave(config);

    expect(sanitized.roles?.director).toEqual({
      provider_id: 'keep',
      model: 'keep-model',
      profile: 'director-default',
    });
  });

  it('removes an empty model while keeping a valid provider binding', () => {
    const config: TestLlmConfig = {
      providers: {
        keep: { name: 'Keep Provider', model: 'keep-model' },
      },
      roles: {
        qa: { provider_id: 'keep', model: '   ', profile: 'qa-strict' },
      },
    };

    const sanitized = sanitizeLlmConfigForSave(config);

    expect(sanitized.roles?.qa).toEqual({
      provider_id: 'keep',
      profile: 'qa-strict',
    });
  });

  it('returns the original object when no cleanup is required', () => {
    const config: TestLlmConfig = {
      providers: {
        keep: { name: 'Keep Provider', model: 'keep-model' },
      },
      roles: {
        pm: { provider_id: 'keep', model: 'keep-model', profile: 'pm-default' },
      },
    };

    expect(sanitizeLlmConfigForSave(config)).toBe(config);
  });

  it('treats missing providers as no available providers and clears role bindings', () => {
    const config: TestLlmConfig = {
      roles: {
        architect: { provider_id: 'ghost', model: 'ghost-model', profile: 'architect-writer' },
      },
    };

    const sanitized = sanitizeLlmConfigForSave(config);

    expect(sanitized.roles?.architect).toEqual({ profile: 'architect-writer' });
  });

  it('ignores malformed roles without rewriting the config', () => {
    const config = {
      providers: {
        keep: { name: 'Keep Provider' },
      },
      roles: [] as unknown as Record<string, RoleConfig>,
    };

    expect(sanitizeLlmConfigForSave(config)).toBe(config);
  });
});
