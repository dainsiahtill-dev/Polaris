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
  fetchStatus?: (workspace: string) => Promise<unknown>;
}

const EMPTY_LLM_RUNTIME_STATE: LlmRuntimeGateState = {
  state: 'UNKNOWN',
  blockedRoles: [],
  requiredRoles: [],
  lastUpdated: null,
};

async function fetchLlmStatusPayload(workspace = ''): Promise<unknown> {
  const suffix = workspace ? `?workspace=${encodeURIComponent(workspace)}` : '';
  const response = await apiFetch(`/v2/llm/status${suffix}`);
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

export function getRoleLlmBlockedReason(
  state: LlmRuntimeGateState,
  role: string,
  roleDisplayName?: string,
): string {
  if (!isRoleLlmBlocked(state, role)) {
    return '';
  }
  const displayName = String(roleDisplayName || role || '当前角色').trim() || '当前角色';
  return `LLM 就绪检查未通过：${displayName} 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。`;
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
      const payload = await fetchStatus(workspace);
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

  const getLlmRoleBlockedReason = useCallback(
    (role: string, roleDisplayName?: string) =>
      getRoleLlmBlockedReason(llmRuntimeState, role, roleDisplayName),
    [llmRuntimeState],
  );

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
    getLlmRoleBlockedReason,
    handleLlmStatusChange,
    refreshLlmGate,
  };
}
