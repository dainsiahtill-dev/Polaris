import { act, renderHook, waitFor } from '@testing-library/react';
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

  it('applies incoming runtime llm status and exposes the Director blocked reason', () => {
    const refreshFetch = vi.fn().mockResolvedValue({
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
      fetchStatus: refreshFetch,
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

  it('actively rechecks a blocked state even when websocket status exists', async () => {
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
      blockedRefreshIntervalMs: 60_000,
      fetchStatus,
    }));

    await waitFor(() => expect(fetchStatus).toHaveBeenCalledTimes(1));
    expect(fetchStatus).toHaveBeenCalledWith('C:/Temp/Product');
    await waitFor(() => expect(result.current.llmRuntimeState.state).toBe('READY'));
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('does not regress to an older blocked websocket snapshot after a newer ready refresh', async () => {
    const fetchStatus = vi.fn().mockResolvedValue({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
      last_updated: '2026-05-24T00:01:00Z',
    });
    const staleBlockedStatus = llmStatus({
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
        blockedRefreshIntervalMs: 60_000,
        fetchStatus,
      }),
      {
        initialProps: {
          status: staleBlockedStatus,
        },
      },
    );

    await waitFor(() => expect(result.current.llmRuntimeState.state).toBe('READY'));

    rerender({
      status: llmStatus({
        state: 'BLOCKED',
        blocked_roles: ['pm'],
        required_ready_roles: ['pm'],
        last_updated: '2026-05-24T00:00:00Z',
      }),
    });

    expect(result.current.llmRuntimeState.state).toBe('READY');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual([]);
  });

  it('keeps the previous blocked state when blocked refresh fails', async () => {
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
      fetchStatus,
    }));

    await waitFor(() => expect(fetchStatus).toHaveBeenCalledTimes(1));
    expect(fetchStatus).toHaveBeenCalledWith('C:/Temp/Product');
    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['pm']);
  });

  it('allows settings callbacks to clear stale blocked state immediately', () => {
    const refreshFetch = vi.fn().mockResolvedValue({
      state: 'BLOCKED',
      blocked_roles: ['pm'],
      required_ready_roles: ['pm'],
    });
    const initialStatus = llmStatus({
      state: 'READY',
      blocked_roles: [],
      required_ready_roles: ['pm'],
    });
    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
      fetchStatus: refreshFetch,
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
