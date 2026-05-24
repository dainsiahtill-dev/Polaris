import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '@/api';
import type { LlmStatus } from '@/app/types/appContracts';

export interface LlmRuntimeGateState {
  state: 'READY' | 'BLOCKED' | 'UNKNOWN';
  blockedRoles: string[];
  requiredRoles: string[];
  lastUpdated: string | null;
}

interface UseLlmRuntimeGateOptions {
  workspace: string;
  live: boolean;
  llmStatus: LlmStatus | null;
  blockedRefreshIntervalMs?: number;
  fetchStatus?: () => Promise<unknown>;
}

const EMPTY_LLM_RUNTIME_STATE: LlmRuntimeGateState = {
  state: 'UNKNOWN',
  blockedRoles: [],
  requiredRoles: [],
  lastUpdated: null,
};

async function fetchLlmStatusPayload(): Promise<unknown> {
  const response = await apiFetch('/v2/llm/status');
  if (!response.ok) {
    throw new Error(`llm status fetch failed: ${response.status}`);
  }
  return response.json();
}

function normalizeRoleList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((role) => String(role || '').trim().toLowerCase())
    .filter(Boolean);
}

function toEpoch(value: string | null): number {
  const parsed = Date.parse(String(value || '').trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function isStaleLlmRuntimePayload(current: LlmRuntimeGateState, next: LlmRuntimeGateState): boolean {
  const currentEpoch = toEpoch(current.lastUpdated);
  const nextEpoch = toEpoch(next.lastUpdated);
  return currentEpoch > 0 && nextEpoch > 0 && nextEpoch < currentEpoch;
}

export function normalizeLlmRuntimeGatePayload(payload: unknown): LlmRuntimeGateState {
  const record = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  const stateToken = String(record.state || '').trim().toUpperCase();
  return {
    state: stateToken === 'READY' ? 'READY' : stateToken === 'BLOCKED' ? 'BLOCKED' : 'UNKNOWN',
    blockedRoles: normalizeRoleList(record.blocked_roles),
    requiredRoles: normalizeRoleList(record.required_ready_roles),
    lastUpdated: typeof record.last_updated === 'string' ? record.last_updated : null,
  };
}

export function isRoleLlmBlocked(state: LlmRuntimeGateState, role: string): boolean {
  const token = String(role || '').trim().toLowerCase();
  if (!token) return false;
  return (
    state.state === 'BLOCKED'
    && state.requiredRoles.includes(token)
    && state.blockedRoles.includes(token)
  );
}

export function useLlmRuntimeGate({
  workspace,
  live,
  llmStatus,
  blockedRefreshIntervalMs = 15_000,
  fetchStatus = fetchLlmStatusPayload,
}: UseLlmRuntimeGateOptions) {
  const [llmRuntimeState, setLlmRuntimeState] = useState<LlmRuntimeGateState>(EMPTY_LLM_RUNTIME_STATE);

  const clearLlmRuntimeState = useCallback(() => {
    setLlmRuntimeState(EMPTY_LLM_RUNTIME_STATE);
  }, []);

  const applyLlmStatusPayload = useCallback((payload: unknown) => {
    const nextState = normalizeLlmRuntimeGatePayload(payload);
    setLlmRuntimeState((current) => (
      isStaleLlmRuntimePayload(current, nextState) ? current : nextState
    ));
  }, []);

  const refreshLlmGate = useCallback(async (options: { clearOnFailure?: boolean } = {}) => {
    if (!workspace) {
      clearLlmRuntimeState();
      return null;
    }

    try {
      const payload = await fetchStatus();
      applyLlmStatusPayload(payload);
      return payload;
    } catch {
      if (options.clearOnFailure) {
        clearLlmRuntimeState();
      }
      return null;
    }
  }, [applyLlmStatusPayload, clearLlmRuntimeState, fetchStatus, workspace]);

  const handleLlmStatusChange = useCallback((status: LlmStatus | null) => {
    if (status) {
      applyLlmStatusPayload(status);
      return;
    }
    clearLlmRuntimeState();
  }, [applyLlmStatusPayload, clearLlmRuntimeState]);

  useEffect(() => {
    if (!workspace) {
      clearLlmRuntimeState();
      return;
    }
    if (llmStatus) {
      applyLlmStatusPayload(llmStatus);
    }
  }, [applyLlmStatusPayload, clearLlmRuntimeState, llmStatus, workspace]);

  useEffect(() => {
    if (!workspace) return;
    if (!live || !llmStatus) {
      void refreshLlmGate({ clearOnFailure: true });
    }
  }, [live, llmStatus, refreshLlmGate, workspace]);

  useEffect(() => {
    if (!workspace || llmRuntimeState.state !== 'BLOCKED') return;

    void refreshLlmGate({ clearOnFailure: false });
    const timer = window.setInterval(() => {
      void refreshLlmGate({ clearOnFailure: false });
    }, blockedRefreshIntervalMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [blockedRefreshIntervalMs, llmRuntimeState.state, refreshLlmGate, workspace]);

  return {
    llmRuntimeState,
    llmDirectorBlockedReason: isRoleLlmBlocked(llmRuntimeState, 'director') ? 'LLM 就绪检查未通过' : '',
    handleLlmStatusChange,
    refreshLlmGate,
  };
}
