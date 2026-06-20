import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  getRoleLlmBlockedReason,
  isRoleLlmBlocked,
  normalizeLlmRuntimeGatePayload,
  useLlmRuntimeGate,
} from '../useLlmRuntimeGate';
import type { LlmStatus } from '@/app/types/appContracts';

function llmStatus(payload: Partial<LlmStatus>): LlmStatus {
  return payload as LlmStatus;
}

describe('useLlmRuntimeGate', () => {
  it('normalizes readiness payload roles and detects role-specific blocks', () => {
    const state = normalizeLlmRuntimeGatePayload({
      state: 'blocked',
      blocked_roles: [' PM ', 'Director'],
      required_ready_roles: ['pm', 'director'],
      last_updated: '2026-05-24T00:00:00Z',
    });

    expect(state).toEqual({
      state: 'BLOCKED',
      blockedRoles: ['pm', 'director'],
      requiredRoles: ['pm', 'director'],
      lastUpdated: '2026-05-24T00:00:00Z',
      roleDetails: {},
    });
    expect(isRoleLlmBlocked(state, 'pm')).toBe(true);
    expect(isRoleLlmBlocked(state, 'qa')).toBe(false);
    expect(getRoleLlmBlockedReason(state, 'pm', 'PM')).toBe(
      'LLM 就绪检查未通过：PM 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。',
    );
    expect(getRoleLlmBlockedReason(state, 'qa', 'QA')).toBe('');
  });

  it('includes configured and tested provider/model details in blocked role reasons', () => {
    const state = normalizeLlmRuntimeGatePayload({
      state: 'blocked',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:00:00Z',
      roles: {
        pm: {
          provider_id: 'codex_cli',
          model: 'gpt-5.3-codex',
          ready: false,
          runtime_supported: true,
          readiness_issue: 'provider_mismatch',
          tested_provider_id: 'anthropic_compat-1779808433822',
          tested_model: 'deepseek-v4-pro',
          tested_timestamp: '2026-05-23T23:59:00Z',
        },
      },
    });

    const reason = getRoleLlmBlockedReason(state, 'pm', 'PM');

    expect(reason).toContain('PM 当前绑定 codex_cli / gpt-5.3-codex');
    expect(reason).toContain('最近测试记录的 Provider 不是当前绑定 Provider');
    expect(reason).toContain('anthropic_compat-1779808433822 / deepseek-v4-pro');
    expect(reason).toContain('2026-05-23T23:59:00Z');
  });

  it('normalizes role binding context windows from llm status', () => {
    const state = normalizeLlmRuntimeGatePayload({
      state: 'ready',
      blocked_roles: [],
      required_ready_roles: ['pm', 'director'],
      roles: {
        pm: {
          provider_id: 'kimi',
          provider_name: 'Kimi Coding',
          provider_type: 'anthropic_compat',
          model: 'kimi-for-coding',
          max_context_tokens: 262144,
          max_output_tokens: 16384,
          bindings: [
            {
              provider_id: 'kimi',
              provider_name: 'Kimi Coding',
              provider_type: 'anthropic_compat',
              model: 'kimi-for-coding',
              max_context_tokens: 262144,
              max_output_tokens: 16384,
            },
          ],
        },
        director: {
          provider_id: 'qwen-a',
          provider_name: 'Qwen A',
          provider_type: 'openai_compat',
          model: 'qwen3.6-27b-gpu0',
          max_context_tokens: 32768,
          bindings: [
            { provider_id: 'qwen-a', model: 'qwen3.6-27b-gpu0', max_context_tokens: 32768 },
            { provider_id: 'qwen-b', model: 'qwen3.6-27b-gpu1', max_context_tokens: 65536 },
          ],
        },
      },
    });

    expect(state.roleDetails?.pm?.providerName).toBe('Kimi Coding');
    expect(state.roleDetails?.pm?.maxContextTokens).toBe(262144);
    expect(state.roleDetails?.pm?.bindings[0]?.maxOutputTokens).toBe(16384);
    expect(state.roleDetails?.director?.bindings.map((binding) => binding.maxContextTokens)).toEqual([32768, 65536]);
  });

  it('treats deprecated readiness_stale blocks as ready', () => {
    const state = normalizeLlmRuntimeGatePayload({
      state: 'blocked',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
      last_updated: '2026-06-15T00:00:00Z',
      roles: {
        pm: {
          provider_id: 'minimax-1781012971065',
          model: 'MiniMax-M3',
          ready: false,
          runtime_supported: true,
          readiness_issue: 'readiness_stale',
          tested_provider_id: 'minimax-1781012971065',
          tested_model: 'MiniMax-M3',
          tested_timestamp: '2026-06-12T00:37:38.823949+00:00',
        },
      },
    });

    expect(state.state).toBe('READY');
    expect(state.blockedRoles).toEqual([]);
    expect(isRoleLlmBlocked(state, 'pm')).toBe(false);
    expect(getRoleLlmBlockedReason(state, 'pm', 'PM')).toBe('');
  });

  it('applies incoming runtime llm status and exposes the Director blocked reason', () => {
    const initialStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['director'],
      required_ready_roles: ['director'],
      roles: {
        director: {
          provider_id: 'codex_cli',
          model: 'gpt-5.3-codex',
          readiness_issue: 'role_readiness_missing',
        },
      },
    });
    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
    }));

    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['director']);
    expect(result.current.getLlmRoleBlockedReason('director', 'Director')).toContain(
      'Director 当前绑定 codex_cli / gpt-5.3-codex',
    );
    expect(result.current.getLlmRoleBlockedReason('director', 'Director')).toContain(
      '该角色还没有通过必需的深度测试',
    );
    expect(result.current.getLlmRoleBlockedReason('pm', 'PM')).toBe('');
  });

  it('does not auto-fetch when websocket status is blocked', () => {
    const fetchStatus = vi.fn().mockResolvedValue({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:01:00Z',
    });
    const initialStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:00:00Z',
    });

    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
    }));

    expect(fetchStatus).not.toHaveBeenCalled();
    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['pm']);
  });

  it('does not auto-fetch when websocket status is missing or disconnected', () => {
    const fetchStatus = vi.fn().mockResolvedValue({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:01:00Z',
    });

    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: false,
      llmStatus: null,
    }));

    expect(fetchStatus).not.toHaveBeenCalled();
    expect(result.current.llmRuntimeState.state).toBe('UNKNOWN');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('clears a stale ready state when the runtime websocket disconnects', () => {
    const readyStatus = llmStatus({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:01:00Z',
    });

    const { result, rerender } = renderHook(
      ({ live }) => useLlmRuntimeGate({
        workspace: 'C:/Temp/Product',
        live,
        llmStatus: live ? readyStatus : null,
      }),
      {
        initialProps: { live: true },
      },
    );

    expect(result.current.llmRuntimeState.state).toBe('READY');

    rerender({ live: false });

    expect(result.current.llmRuntimeState.state).toBe('UNKNOWN');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('ignores direct status callbacks while the runtime websocket is disconnected', () => {
    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: false,
      llmStatus: null,
    }));

    act(() => {
      result.current.handleLlmStatusChange(llmStatus({
        state: 'READY',
        blocked_roles: [],
        required_ready_roles: ['pm'],
      }));
    });

    expect(result.current.llmRuntimeState.state).toBe('UNKNOWN');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('does not regress to an older blocked websocket snapshot after a newer ready stream event', () => {
    const readyStatus = llmStatus({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:01:00Z',
    });
    const olderBlockedStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:00:00Z',
    });

    const { result, rerender } = renderHook(
      ({ status }) => useLlmRuntimeGate({
        workspace: 'C:/Temp/Product',
        live: true,
        llmStatus: status,
      }),
      {
        initialProps: {
          status: readyStatus,
        },
      },
    );

    expect(result.current.llmRuntimeState.state).toBe('READY');

    rerender({
      status: olderBlockedStatus,
    });

    expect(result.current.llmRuntimeState.state).toBe('READY');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('keeps a newer blocked websocket snapshot until another stream event replaces it', () => {
    const mockedBlockedStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm', 'director'],
      last_updated: '2026-05-29T19:30:00Z',
      roles: {
        pm: {
          provider_id: 'qwen-main',
          model: 'qwen3-max-current-with-long-region-routing-label',
          ready: false,
          readiness_issue: 'model_mismatch',
          tested_provider_id: 'qwen-main',
          tested_model: 'qwen3-max-previously-tested-model',
        },
      },
    });

    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: mockedBlockedStatus,
    }));

    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['pm']);
    expect(result.current.getLlmRoleBlockedReason('pm', 'PM')).toContain('最近测试记录的模型不是当前绑定模型');
  });

  it('does not use a failed blocked refresh fallback', () => {
    const fetchStatus = vi.fn().mockRejectedValue(new Error('offline'));
    const initialStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
    });

    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
    }));

    expect(fetchStatus).not.toHaveBeenCalled();
    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['pm']);
  });

  it('allows runtime status callbacks to replace the current stream state', () => {
    const initialStatus = llmStatus({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
    });
    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
    }));

    act(() => {
      result.current.handleLlmStatusChange(llmStatus({
        state: 'BLOCKED',
        blocked_roles: ['pm'],
        required_ready_roles: ['pm'],
      }));
    });
    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');

    act(() => {
      result.current.handleLlmStatusChange(llmStatus({
        state: 'READY',
        blocked_roles: [],
        required_ready_roles: ['pm'],
      }));
    });
    expect(result.current.llmRuntimeState.state).toBe('READY');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });
});
