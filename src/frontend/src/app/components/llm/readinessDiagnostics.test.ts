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
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('最近测试: Qwen Production/MiniMax-M2.5');
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

  it('shows the precise Director Codex sandbox issue when runtime support is blocked', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      unsupportedRoles: ['director'],
      roles: {
        director: {
          provider_id: 'codex_cli',
          model: 'gpt-5.3-codex',
          runtime_supported: false,
          runtime_issue: 'director_codex_read_only_sandbox',
          ready: true,
        },
      },
      providers: {
        codex_cli: {
          name: 'Codex CLI',
          type: 'codex_cli',
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      issue: 'director_codex_read_only_sandbox',
      issueLabel: 'Director 的 Codex CLI 当前是只读沙箱，无法落盘代码或文档',
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

  it('shows stale readiness records with provider, model, and tested timestamp', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      blockedRoles: ['director'],
      roles: {
        director: {
          provider_id: 'kimi-main',
          model: 'kimi-for-coding',
          ready: false,
          readiness_issue: 'readiness_stale',
          tested_provider_id: 'kimi-main',
          tested_model: 'kimi-for-coding',
          tested_timestamp: '2026-05-25T19:01:09+00:00',
          runtime_supported: true,
        },
      },
      providers: {
        'kimi-main': {
          name: 'Kimi Coding',
          type: 'anthropic_compat',
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      roleLabel: 'Director',
      providerName: 'Kimi Coding',
      configuredModel: 'kimi-for-coding',
      issue: 'readiness_stale',
      issueLabel: '最近测试记录已过期，请重新测试当前 Provider/模型',
      testedTimestamp: '2026-05-25T19:01:09+00:00',
    });
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('Kimi Coding/kimi-for-coding');
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('测试时间: 2026-05-25T19:01:09+00:00');
  });

  it('shows failed readiness records with the tested provider and model', () => {
    const diagnostics = buildBlockedRoleDiagnostics({
      blockedRoles: ['director'],
      roles: {
        director: {
          provider_id: 'codex_cli',
          model: 'gpt-5.3-codex',
          ready: false,
          readiness_issue: 'readiness_failed',
          tested_provider_id: 'codex_cli',
          tested_model: 'gpt-5.3-codex',
          tested_timestamp: '2026-05-28T08:40:29+00:00',
          runtime_supported: true,
        },
      },
      providers: {
        codex_cli: {
          name: 'Codex CLI',
          type: 'codex_cli',
        },
      },
    });

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      roleLabel: 'Director',
      providerName: 'Codex CLI',
      configuredModel: 'gpt-5.3-codex',
      issue: 'readiness_failed',
      issueLabel: '最近一次深度测试失败，请重新测试或切换 Provider/模型',
      testedProviderName: 'Codex CLI',
      testedModel: 'gpt-5.3-codex',
      testedTimestamp: '2026-05-28T08:40:29+00:00',
    });
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('Director: Codex CLI/gpt-5.3-codex');
    expect(formatBlockedRoleTitle(diagnostics[0])).toContain('最近测试: Codex CLI/gpt-5.3-codex');
  });
});
