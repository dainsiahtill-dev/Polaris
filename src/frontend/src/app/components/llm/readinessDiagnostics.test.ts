import { describe, expect, it } from 'vitest';
import { buildBlockedRoleDiagnostics, formatBlockedRoleTitle } from './readinessDiagnostics';

describe('readinessDiagnostics', () => {
  it('shows the configured provider, model, tested binding, and mismatch reason for blocked roles', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      blockedRoles: ['pm'],
      roles: {
        pm: {
          provider_id: 'qwen-main',
          model: 'Qwen3-Max',
          ready: false,
          readiness_issue: 'model_mismatch',
          tested_provider_id: 'qwen-main',
          tested_model: 'MiniMax-M2.5',
          runtime_supported: true,
        },
      },
      providers: {
        'qwen-main': {
          name: 'Qwen Production',
          type: 'openai_compat',
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      roleLabel: 'PM',
      providerId: 'qwen-main',
      providerName: 'Qwen Production',
      configuredModel: 'Qwen3-Max',
      testedProviderId: 'qwen-main',
      testedProviderName: 'Qwen Production',
      testedModel: 'MiniMax-M2.5',
      issueLabel: '最近通过测试的模型不是当前绑定模型',
    });
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('Qwen Production/Qwen3-Max');
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('最近通过: Qwen Production/MiniMax-M2.5');
  });

  it('deduplicates blocked and unsupported roles while preserving provider diagnostics', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      blockedRoles: ['director'],
      unsupportedRoles: ['Director'],
      roles: {
        director: {
          provider_id: 'codex-cli',
          model: 'gpt-5-codex',
          runtime_supported: false,
        },
      },
      providers: {
        'codex-cli': {
          name: 'Codex CLI',
          type: 'codex_cli',
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      roleLabel: 'Director',
      providerName: 'Codex CLI',
      configuredModel: 'gpt-5-codex',
      issue: 'runtime_unsupported',
      issueLabel: '当前 Provider 类型不支持该角色运行时',
      runtimeSupported: false,
    });
  });

  it('makes missing role bindings explicit instead of only returning the role name', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      blockedRoles: ['qa'],
      roles: {
        qa: {
          ready: false,
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      roleLabel: 'QA',
      providerName: '未绑定 Provider',
      configuredModel: '未绑定模型',
      issue: 'unassigned_provider',
      issueLabel: '该角色未绑定 Provider',
    });
  });
});
