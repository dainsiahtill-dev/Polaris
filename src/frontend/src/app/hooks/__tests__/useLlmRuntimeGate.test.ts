import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
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
    });
    expect(isRoleLlmBlocked(state, 'pm')).toBe(true);
    expect(isRoleLlmBlocked(state, 'qa')).toBe(false);
  });

  it('applies incoming runtime llm status and exposes the Director blocked reason', () => {
    const refreshFetch = vi.fn().mockResolvedValue({
      state: 'BLOCKED',
      blocked_roles: ['director'],
      required_ready_roles: ['director'],
    });
    const initialStatus = llmStatus({
      state: 'BLOCKED',
      blocked_roles: ['director'],
      required_ready_roles: ['director'],
    });
    const { result } = renderHook(() => useLlmRuntimeGate({
      workspace: 'C:/Temp/Product',
      live: true,
      llmStatus: initialStatus,
      fetchStatus: refreshFetch,
    }));

    expect(result.current.llmRuntimeState.state).toBe('BLOCKED');
    expect(result.current.llmRuntimeState.blockedRoles).toEqual(['director']);
    expect(result.current.llmDirectorBlockedReason).toBe('LLM 就绪检查未通过');
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
