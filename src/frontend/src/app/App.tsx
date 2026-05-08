import { lazy, Suspense, useCallback, useEffect, useRef, useMemo, useState } from 'react';
import { Panel, PanelGroup, PanelResizeHandle, ImperativePanelHandle } from 'react-resizable-panels';
import { toast } from 'sonner';
import { Toaster } from './components/ui/sonner';

import {
  useProcessOperations,
  useUIState,
  useSettings,
  useFileManager,
  useMemos,
  useMemory,
  useNotifications,
  useAgentsReview,
} from '@/hooks';

import { ControlPanel } from '@/app/components/ControlPanel';
import { RealTimeStatusBar } from '@/app/components/RealTimeStatusBar';
import { devLogger } from '@/app/utils/devLogger';
import { ContextSidebar } from '@/app/components/ContextSidebar';
import { ProjectProgressPanel } from '@/app/components/ProjectProgressPanel';
import { HistoryDrawer } from '@/app/components/HistoryDrawer';
import { EnhancedNotificationManager } from '@/app/components/EnhancedNotificationManager';
import { AgentsReviewDialog } from '@/app/components/AgentsReviewDialog';
import { ErrorBoundaryClass } from '@/app/components/ErrorBoundary';
import { RuntimeErrorDialog } from '@/app/components/RuntimeErrorDialog';
import { PMWorkspace } from '@/app/components/pm';
import { DirectorWorkspace } from '@/app/components/director';
import { ChiefEngineerWorkspace } from '@/app/components/chief-engineer';
import { FactoryWorkspace } from '@/app/components/factory/FactoryWorkspace';
import { ResidentWorkspace } from '@/app/components/resident';
import { LlmRuntimeOverlay } from '@/app/components/LlmRuntimeOverlay';
import { RuntimeDiagnosticsWorkspace } from '@/app/components/RuntimeDiagnosticsWorkspace';
import { apiFetch, apiFetchFresh, openPath, pickWorkspace } from '@/api';
import { useRuntime } from './hooks/useRuntime';
import { useRuntimeConnectionNotifications } from './hooks/useConnectionNotifications';
import { RuntimeTransportProvider } from '@/runtime/transport';
import { useLiveTaskQueues } from './hooks/useLiveTaskQueues';
import { useUsageStats } from './hooks/useUsageStats';
import { useFactory } from '@/hooks/useFactory';
import { getLatestExecutionActivityLog } from '@/app/utils/appRuntime';
import { isLancedbExplicitlyBlocked } from '@/app/utils/lancedbGate';
import { normalizeStartedAtSeconds } from '@/app/utils/runtimeDisplay';

// Lazy load pages

import type { PmTask } from '@/types/task';
import type { BackendStatus, RuntimeIssue, SnapshotPayload } from '@/app/types/appContracts';
import { resolveRunning } from '@/types/app';

const ProcessMonitorSidebar = lazy(() =>
  import('./components/ProcessMonitorSidebar').then(m => ({ default: m.ProcessMonitorSidebar }))
);
const SettingsModal = lazy(() =>
  import('./components/SettingsModal').then(m => ({ default: m.SettingsModal }))
);
const DocsInitDialog = lazy(() =>
  import('./components/DocsInitDialog').then(m => ({ default: m.DocsInitDialog }))
);
const LogsModal = lazy(() =>
  import('./components/LogsModal').then(m => ({ default: m.LogsModal }))
);
const TerminalPanel = lazy(() =>
  import('./components/TerminalPanel').then(m => ({ default: m.TerminalPanel }))
);

const RUN_ID_PREFIX = 'pm-';

function parseIterationFromRunId(runId: string): number | null {
  const raw = runId.trim().toLowerCase();
  if (!raw.startsWith(RUN_ID_PREFIX)) return null;
  const suffix = raw.slice(RUN_ID_PREFIX.length);
  if (!/^\d+$/.test(suffix)) return null;
  const value = Number(suffix);
  return Number.isFinite(value) ? value : null;
}

function toIterationValue(snapshot: SnapshotPayload | null): number | null {
  if (!snapshot || typeof snapshot !== 'object') return null;

  const runId = typeof snapshot.run_id === 'string' ? snapshot.run_id : '';
  const fromRunId = parseIterationFromRunId(runId);
  if (fromRunId !== null) return fromRunId;

  const pmState = snapshot.pm_state;
  if (!pmState || typeof pmState !== 'object') return null;
  const raw = pmState['pm_iteration'];
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const parsed = Number(raw.trim());
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function toRunKey(snapshot: SnapshotPayload | null): string {
  if (!snapshot || typeof snapshot !== 'object') return '';
  const runId = typeof snapshot.run_id === 'string' ? snapshot.run_id.trim() : '';
  if (runId) return runId;
  const iteration = toIterationValue(snapshot);
  return iteration !== null ? `${RUN_ID_PREFIX}${String(iteration).padStart(5, '0')}` : '';
}

function isDoneLikeStatus(value: unknown): boolean {
  const status = String(value || '').trim().toLowerCase();
  if (!status) return false;
  return ['done', 'complete', 'completed', 'success', 'passed', 'pass', 'ok'].some((token) =>
    status.includes(token)
  );
}

function areSnapshotTasksDone(snapshot: SnapshotPayload | null): boolean {
  const tasks = Array.isArray(snapshot?.tasks) ? snapshot.tasks : [];
  if (!tasks.length) return false;
  return tasks.every((task) => {
    if (!task || typeof task !== 'object') return false;
    const candidate = task as Record<string, unknown>;
    if (candidate.done === true || candidate.completed === true) return true;
    return isDoneLikeStatus(candidate.status) || isDoneLikeStatus(candidate.state);
  });
}

function countSnapshotTasks(snapshot: SnapshotPayload | null): number {
  if (!Array.isArray(snapshot?.tasks)) {
    return 0;
  }
  return snapshot.tasks.filter((task) => Boolean(task && typeof task === 'object')).length;
}

function getSnapshotDirectorStatus(snapshot: SnapshotPayload | null): string {
  const pmState = snapshot?.pm_state;
  if (!pmState || typeof pmState !== 'object') {
    return '';
  }
  return typeof pmState['last_director_status'] === 'string'
    ? pmState['last_director_status'].trim()
    : '';
}

function shouldKeepRicherSnapshot(
  previous: SnapshotPayload | null,
  incoming: SnapshotPayload | null,
): boolean {
  const previousTaskCount = countSnapshotTasks(previous);
  const incomingTaskCount = countSnapshotTasks(incoming);
  if (previousTaskCount > 0 && incomingTaskCount === 0) {
    return true;
  }

  const previousDirectorStatus = getSnapshotDirectorStatus(previous);
  const incomingDirectorStatus = getSnapshotDirectorStatus(incoming);
  if (previousDirectorStatus && !incomingDirectorStatus && previousTaskCount >= incomingTaskCount) {
    return true;
  }

  return false;
}

function isActiveRuntimePhase(value: string): boolean {
  const token = String(value || '').trim().toLowerCase();
  return Boolean(token && !['idle', 'unknown', 'none'].includes(token));
}

function isPmTerminalFailure(status: BackendStatus | null): boolean {
  if (!status) return false;
  const statusToken = String(status.status || '').trim().toLowerCase();
  const exitCode = typeof status.exit_code === 'number' ? status.exit_code : null;
  const error = String(status.error || '').trim();
  return (
    (exitCode !== null && exitCode !== 0)
    || status.ok === false
    || (status.terminal === true && statusToken === 'failed')
    || Boolean(error)
  );
}

function isPmRuntimeIssue(issue: RuntimeIssue | null): boolean {
  if (!issue) return false;
  const token = `${issue.code || ''} ${issue.title || ''}`.toUpperCase();
  return (
    token.includes('PM')
    || token.includes('ENGINE_RUNTIME_FAILED')
    || token.includes('POLARIS 引擎执行失败')
  );
}

function resolveEffectivePmRunning(status: BackendStatus | null, issue: RuntimeIssue | null): boolean {
  if (isPmTerminalFailure(status) || isPmRuntimeIssue(issue)) {
    return false;
  }
  return Boolean(status?.running);
}

function resolveEffectivePhase(currentPhase: string, pmRunning: boolean, issue: RuntimeIssue | null): string {
  if (isPmRuntimeIssue(issue)) return 'error';
  const token = String(currentPhase || '').trim().toLowerCase();
  if (!pmRunning && ['planning', 'analyzing', 'llm_calling'].includes(token)) {
    return 'idle';
  }
  return currentPhase;
}

function AppContent() {
  const workspacePanelRef = useRef<ImperativePanelHandle>(null);
  const terminalPanelRef = useRef<ImperativePanelHandle>(null);

  const { state: ui, actions: uiActions } = useUIState();
  const [activeRoleView, setActiveRoleView] = useState<'main' | 'pm' | 'chief_engineer' | 'director' | 'factory' | 'agi' | 'diagnostics'>('main');
  const { settings, load: loadSettings, update: updateSettings } = useSettings();
  const { notifications, remove: removeNotification, error: notifyError } = useNotifications();
  const workspace = settings?.workspace || '';

  const {
    live,
    reconnecting,
    attemptCount,
    pmStatus,
    directorStatus,
    engineStatus,
    llmStatus,
    lancedbStatus,
    snapshot,
    anthroState,
    dialogueEvents,
    qualityGate,
    executionLogs,
    llmStreamEvents,
    processStreamEvents,
    currentPhase,
    fileEditEvents,
    reconnect: reconnectWebSocket,
    tasks: runtimeTasks,
    workers: runtimeWorkers,
    isConnected: runtimeConnected,
    runId: runtimeRunId,
    taskProgressMap,
    taskTraceMap,
  } = useRuntime({ roles: ['pm', 'chief_engineer', 'director', 'qa'], workspace });

  // Connection status notifications (WebSocket fallback alerts)
  useRuntimeConnectionNotifications({
    live,
    reconnecting,
    reconnect: reconnectWebSocket,
  });

  // Factory run state
  const {
    currentRun: factoryCurrentRun,
    events: factoryEvents,
    artifacts: factoryArtifacts,
    summaryMd: factorySummaryMd,
    summaryJson: factorySummaryJson,
    artifactsError: factoryArtifactsError,
    isArtifactsLoading: factoryArtifactsLoading,
    startRun: startFactoryRun,
    stopRun: stopFactoryRun,
    isLoading: factoryIsLoading,
  } = useFactory({ workspace });

  const { stats: usageStats, loading: usageLoading, error: usageError, refresh: refreshUsage } = useUsageStats(workspace || null);
  const directorRunning = resolveRunning(directorStatus);
  const lancedbBlocked = isLancedbExplicitlyBlocked(lancedbStatus);
  const latestProcessActivity = useMemo(
    () => getLatestExecutionActivityLog(processStreamEvents),
    [processStreamEvents],
  );
  const [progressSnapshot, setProgressSnapshot] = useState<SnapshotPayload | null>(null);
  const [llmDirectorBlockedReason, setLlmDirectorBlockedReason] = useState('');
  const [llmRuntimeState, setLlmRuntimeState] = useState<{
    state: 'READY' | 'BLOCKED' | 'UNKNOWN';
    blockedRoles: string[];
    requiredRoles: string[];
    lastUpdated: string | null;
  }>({
    state: 'UNKNOWN',
    blockedRoles: [],
    requiredRoles: [],
    lastUpdated: null,
  });

  useEffect(() => {
    if (!snapshot) return;
    setProgressSnapshot((previous) => {
      if (!previous) return snapshot;

      const previousRun = toRunKey(previous);
      const incomingRun = toRunKey(snapshot);
      if (!previousRun || !incomingRun || previousRun === incomingRun) {
        if (shouldKeepRicherSnapshot(previous, snapshot)) {
          return previous;
        }
        return snapshot;
      }

      const previousDone = areSnapshotTasksDone(previous);
      const incomingDone = areSnapshotTasksDone(snapshot);

      // Keep displaying the completed run when the next run is still incomplete,
      // so the main panel does not visually "roll back" progress.
      if (previousDone && !incomingDone) {
        return previous;
      }

      return snapshot;
    });
  }, [snapshot]);

  const displaySnapshot = progressSnapshot ?? snapshot;

  const refreshProgressSnapshot = useCallback(async () => {
    try {
      const response = await apiFetchFresh('/state/snapshot');
      if (!response.ok) {
        return null;
      }
      const payload = (await response.json()) as SnapshotPayload | null;
      if (!payload || typeof payload !== 'object') {
        return null;
      }
      setProgressSnapshot(payload);
      return payload;
    } catch {
      return null;
    }
  }, []);

  const {
    isStartingPM,
    isStoppingPM,
    isStartingDirector,
    isStoppingDirector,
    pmActionError,
    directorActionError,
    togglePm,
    runPmOnce,
    toggleDirector,
    clearPmError,
    clearDirectorError,
  } = useProcessOperations({
    onStatusChange: () => {
      loadSettings();
      // 不需要手动 reconnectWebSocket() - 状态已通过现有 WebSocket 实时推送
      void refreshProgressSnapshot();
    },
    onOpenLogs: (sourceId, banner) => {
      uiActions.openLogs(sourceId, banner);
    },
    lancedbBlocked,
    lancedbBlockMessage: lancedbBlocked ? lancedbStatus?.error ?? undefined : undefined,
  });

  const fileManager = useFileManager({ workspace });
  const memos = useMemos({ workspace });
  const memory = useMemory({
    showMemory: settings?.show_memory,
    workspace,
    ramdiskRoot: settings?.ramdisk_root
  });

  const agentsReview = useAgentsReview({
    agentsReview: snapshot?.agents_review ?? null,
    isOpen: ui.isAgentsDialogOpen,
    runtimeIssue: snapshot?.runtime_issues?.[0],
  });

  const liveSnapshotTasks = useMemo(() => {
    if (!Array.isArray(snapshot?.tasks)) return [];
    return snapshot.tasks.filter((task): task is PmTask =>
      Boolean(task && typeof task === 'object')
    );
  }, [snapshot]);
  const displaySnapshotTasks = useMemo(() => {
    if (!Array.isArray(displaySnapshot?.tasks)) return [];
    return displaySnapshot.tasks.filter((task): task is PmTask =>
      Boolean(task && typeof task === 'object')
    );
  }, [displaySnapshot]);

  const liveTaskQueues = useLiveTaskQueues({
    snapshotTasks: liveSnapshotTasks,
    directorRealtime: {
      tasks: runtimeTasks as PmTask[],
      isConnected: runtimeConnected,
      runId: runtimeRunId,
    },
  });
  const displayTaskQueues = useLiveTaskQueues({
    snapshotTasks: displaySnapshotTasks,
    directorRealtime: {
      tasks: runtimeTasks as PmTask[],
      isConnected: runtimeConnected,
      runId: runtimeRunId,
    },
  });

  const {
    pmTasks,
    directorTasks,
    directorTaskSource,
    isDirectorRealtimeConnected,
  } = liveTaskQueues;
  const {
    pmTasks: progressPmTasks,
    directorTasks: progressDirectorTasks,
    directorTaskSource: progressDirectorTaskSource,
    isDirectorRealtimeConnected: isProgressDirectorRealtimeConnected,
  } = displayTaskQueues;
  const directorWorkspaceTasks = progressDirectorTasks.length > 0 ? progressDirectorTasks : directorTasks;
  const directorSeedTasks = progressPmTasks.length > 0 ? progressPmTasks : pmTasks;

  const runtimeIssue: RuntimeIssue | null = useMemo(() =>
    snapshot?.runtime_issues?.[0] ?? null,
    [snapshot?.runtime_issues]
  );
  const engineRuntimeIssue: RuntimeIssue | null = useMemo(() => {
    if (!engineStatus) return null;

    const phase = String(engineStatus.phase || '').trim().toLowerCase();
    const errorCode = String(engineStatus.error || '').trim();
    if (!errorCode && phase !== 'failed') return null;

    const detailLines: string[] = [];
    if (phase) detailLines.push(`阶段: ${phase}`);

    const roles = engineStatus.roles && typeof engineStatus.roles === 'object'
      ? engineStatus.roles
      : null;
    const directorDetail = String(roles?.Director?.detail || '').trim();
    const qaDetail = String(roles?.QA?.detail || '').trim();
    if (directorDetail) detailLines.push(`Director: ${directorDetail}`);
    if (qaDetail) detailLines.push(`QA: ${qaDetail}`);

    const summary = engineStatus.summary && typeof engineStatus.summary === 'object'
      ? engineStatus.summary
      : null;
    const total = Number(summary?.total || 0);
    const failures = Number(summary?.failures || 0);
    const blocked = Number(summary?.blocked || 0);
    if (total > 0 || failures > 0 || blocked > 0) {
      detailLines.push(`任务统计: total=${total}, failures=${failures}, blocked=${blocked}`);
    }

    const failedTasks = Array.isArray(snapshot?.tasks)
      ? snapshot.tasks
        .filter((task): task is Record<string, unknown> => Boolean(task && typeof task === 'object'))
        .filter((task) => {
          const status = String(task.status || task.state || '').trim().toLowerCase();
          return status === 'blocked' || status === 'failed';
        })
      : [];
    if (failedTasks.length > 0) {
      detailLines.push('阻塞任务:');
      for (const task of failedTasks.slice(0, 3)) {
        const taskId = String(task.id || task.title || 'unknown').trim() || 'unknown';
        const taskError = String(task.failure_detail || task.error_code || task.reason || '').trim();
        detailLines.push(`- ${taskId}: ${taskError || '执行失败（无详细信息）'}`);
      }
      if (failedTasks.length > 3) {
        detailLines.push(`- ... 另有 ${failedTasks.length - 3} 个阻塞任务`);
      }
    }

    if (!detailLines.length) {
      detailLines.push('引擎执行失败，请查看运行日志。');
    }

    return {
      code: errorCode || 'ENGINE_RUNTIME_FAILED',
      title: 'Polaris 引擎执行失败',
      detail: detailLines.join('\n'),
    };
  }, [engineStatus, snapshot?.tasks]);

  const actionRuntimeIssue: RuntimeIssue | null = useMemo(() => {
    if (pmActionError) {
      return {
        code: 'PM_ACTION_FAILED',
        title: 'PM 操作失败',
        detail: pmActionError,
      };
    }
    if (directorActionError) {
      return {
        code: 'DIRECTOR_ACTION_FAILED',
        title: 'Director 操作失败',
        detail: directorActionError,
      };
    }
    return null;
  }, [pmActionError, directorActionError]);

  const pmStateRuntimeIssue: RuntimeIssue | null = useMemo(() => {
    const pmState = snapshot?.pm_state;
    if (!pmState || typeof pmState !== 'object') return null;

    const lastPmCode = String(pmState['last_pm_error_code'] || '').trim();
    const lastPmDetail = String(pmState['last_pm_error_detail'] || '').trim();
    if (lastPmCode) {
      return {
        code: lastPmCode,
        title: 'PM 运行异常',
        detail: lastPmDetail || 'PM 运行失败，请查看日志。',
      };
    }

    const directorCode = String(pmState['last_director_error_code'] || '').trim();
    const directorDetail = String(pmState['last_director_error_detail'] || '').trim();
    if (directorCode && directorCode !== 'PLAN_MISSING') {
      return {
        code: directorCode,
        title: 'Director 链路异常',
        detail: directorDetail || 'Director 执行异常，请查看日志。',
      };
    }

    const manualCode = String(pmState['manual_intervention_reason_code'] || '').trim();
    const manualDetail = String(pmState['manual_intervention_detail'] || '').trim();
    if (manualCode) {
      return {
        code: manualCode,
        title: '流程暂停等待人工介入',
        detail: manualDetail || '请处理阻塞后再继续。',
      };
    }

    return null;
  }, [snapshot?.pm_state]);

  const activeRuntimeIssue: RuntimeIssue | null = useMemo(
    () => runtimeIssue ?? engineRuntimeIssue ?? pmStateRuntimeIssue ?? actionRuntimeIssue,
    [runtimeIssue, engineRuntimeIssue, pmStateRuntimeIssue, actionRuntimeIssue]
  );

  const docsMissing = useMemo(() => {
    if (snapshot?.docs_present === false) return true;
    return snapshot?.workspace_status?.status === 'NEEDS_DOCS_INIT';
  }, [snapshot?.docs_present, snapshot?.workspace_status?.status]);

  const agentsRequired = Boolean(snapshot?.agents_review?.needs_review);
  const agentsDraftReady = Boolean(snapshot?.agents_review?.draft_path);
  const agentsDraftFailed = agentsReview.draftFailed;
  const llmDirectorBlocked = Boolean(llmDirectorBlockedReason);
  const rawPmRunning = Boolean(pmStatus?.running);
  const effectivePmRunning = resolveEffectivePmRunning(pmStatus, activeRuntimeIssue);
  const effectiveCurrentPhase = resolveEffectivePhase(currentPhase, effectivePmRunning, activeRuntimeIssue);
  const llmPmBlocked = (
    llmRuntimeState.state === 'BLOCKED'
    && llmRuntimeState.requiredRoles.includes('pm')
    && llmRuntimeState.blockedRoles.includes('pm')
  );
  const pmStartBlockedReason = llmPmBlocked
    ? 'LLM 就绪检查未通过：PM 角色当前绑定的 provider/model 没有通过真实测试，请先在 LLM 设置中重新测试并保存。'
    : '';

  const applyLlmStatusPayload = useCallback((payload: {
    state?: unknown;
    blocked_roles?: unknown;
    required_ready_roles?: unknown;
    last_updated?: unknown;
  }) => {
    const stateToken = String(payload.state || '').trim().toUpperCase();
    const blockedRoles = Array.isArray(payload.blocked_roles)
      ? payload.blocked_roles.map((role) => String(role).trim().toLowerCase())
      : [];
    const requiredRoles = Array.isArray(payload.required_ready_roles)
      ? payload.required_ready_roles.map((role) => String(role).trim().toLowerCase())
      : [];

    const shouldBlockDirector =
      stateToken === 'BLOCKED' &&
      requiredRoles.includes('director') &&
      blockedRoles.includes('director');

    setLlmRuntimeState({
      state: stateToken === 'READY' ? 'READY' : stateToken === 'BLOCKED' ? 'BLOCKED' : 'UNKNOWN',
      blockedRoles,
      requiredRoles,
      lastUpdated: typeof payload.last_updated === 'string' ? payload.last_updated : null,
    });
    setLlmDirectorBlockedReason(shouldBlockDirector ? 'LLM 就绪检查未通过' : '');
  }, []);

  useEffect(() => {
    if (!llmStatus) return;
    applyLlmStatusPayload(llmStatus);
  }, [llmStatus, applyLlmStatusPayload]);

  // 这里保留一个初始化获取，当 WebSocket 断开时作为降级方案
  useEffect(() => {
    if (!workspace) {
      setLlmDirectorBlockedReason('');
      setLlmRuntimeState({
        state: 'UNKNOWN',
        blockedRoles: [],
        requiredRoles: [],
        lastUpdated: null,
      });
      return;
    }

    let cancelled = false;

    const refreshLlmGate = async () => {
      try {
        const response = await apiFetch('/v2/llm/status');
        if (!response.ok) {
          throw new Error(`llm status fetch failed: ${response.status}`);
        }
        const payload = (await response.json()) as {
          state?: unknown;
          blocked_roles?: unknown;
          required_ready_roles?: unknown;
          last_updated?: unknown;
        };

        if (cancelled) return;

        if (!cancelled) {
          applyLlmStatusPayload(payload);
        }
      } catch {
        if (!cancelled) {
          setLlmRuntimeState({
            state: 'UNKNOWN',
            blockedRoles: [],
            requiredRoles: [],
            lastUpdated: null,
          });
          setLlmDirectorBlockedReason('');
        }
      }
    };

    // 仅在 WebSocket 未连接或未回传 llm_status 时进行降级获取
    if (!live || !llmStatus) {
      void refreshLlmGate();
    }

    return () => {
      cancelled = true;
    };
  }, [workspace, live, llmStatus, applyLlmStatusPayload]);

  const runtimeLlmGateActive = effectivePmRunning || directorRunning || isActiveRuntimePhase(effectiveCurrentPhase);
  const llmStatusForBar = llmRuntimeState.state === 'READY'
    ? 'ready'
    : llmRuntimeState.state === 'BLOCKED' && runtimeLlmGateActive
      ? 'blocked'
      : 'unknown';

  useEffect(() => {
    if (lancedbBlocked) {
      uiActions.openLanceDbDialog();
    } else {
      uiActions.closeLanceDbDialog();
    }
  }, [lancedbBlocked]);

  useEffect(() => {
    if (activeRuntimeIssue) {
      uiActions.openRuntimeDialog();
      uiActions.closeAgentsDialog();
      uiActions.closePlanDialog();
    } else {
      uiActions.closeRuntimeDialog();
    }
  }, [activeRuntimeIssue?.code, activeRuntimeIssue?.detail]);

  useEffect(() => {
    if (activeRuntimeIssue || ui.isAgentsDialogOpen) return;

    const pmState = snapshot?.pm_state;
    const errorCode = String(pmState?.last_director_error_code || '');
    if (errorCode === 'PLAN_MISSING') {
      uiActions.openPlanDialog();
    } else {
      uiActions.closePlanDialog();
    }
  }, [activeRuntimeIssue, ui.isAgentsDialogOpen, snapshot?.pm_state]);

  const handleRuntimeOpenLogs = () => {
    const issueCode = String(activeRuntimeIssue?.code || '').toUpperCase();
    const sourceId = issueCode.includes('DIRECTOR') || issueCode.includes('ENGINE')
      ? 'director'
      : 'pm-subprocess';
    uiActions.closeRuntimeDialog();
    uiActions.openLogs(sourceId);
  };

  const handleRuntimeDismiss = () => {
    clearPmError();
    clearDirectorError();
  };

  const toggleTerminalMaximize = () => {
    const terminal = terminalPanelRef.current;
    const workspacePanel = workspacePanelRef.current;

    if (!terminal || !workspacePanel) return;

    if (ui.isTerminalMaximized) {
      workspacePanel.resize(70);
      terminal.resize(30);
      uiActions.setTerminalMaximize(false);
    } else {
      workspacePanel.collapse();
      uiActions.setTerminalMaximize(true);
    }
  };

  const handleRefresh = () => {
    loadSettings();
    reconnectWebSocket();
  };

  const handlePickWorkspace = async () => {
    try {
      const picked = await pickWorkspace(workspace);
      if (picked) {
        const updated = await updateSettings({ workspace: picked });
        if (updated) {
          // WebSocket will auto-reconnect via useRuntime workspace effect
          toast.success('Workspace updated');
        } else {
          toast.error('Failed to update workspace');
        }
      }
    } catch (err) {
      toast.error('Failed to pick workspace');
    }
  };

  const handleOpenWorkspace = async () => {
    if (!workspace) return;
    try {
      const result = await openPath(workspace);
      if (!result.ok) {
        toast.error('Failed to open workspace folder');
      }
    } catch {
      toast.error('Failed to open workspace');
    }
  };

  const handleSaveSettings = async (payload: Record<string, unknown>) => {
    const updated = await updateSettings(payload);
    if (updated) {
      toast.success('Settings saved');
      return;
    }
    toast.error('Failed to save settings');
  };

  // Role Workspace handlers
  const handleEnterPMWorkspace = () => {
    setActiveRoleView('pm');
    void refreshProgressSnapshot();
  };

  const handleEnterChiefEngineerWorkspace = () => {
    setActiveRoleView('chief_engineer');
    void refreshProgressSnapshot();
  };

  const handleEnterDirectorWorkspace = () => {
    setActiveRoleView('director');
    void refreshProgressSnapshot();
  };

  const handleEnterFactoryMode = () => {
    // Factory 模式：先 PM 规划，再 Director 执行
    setActiveRoleView('factory');
    void refreshProgressSnapshot();
  };

  const handleEnterAGIWorkspace = () => {
    setActiveRoleView('agi');
    void refreshProgressSnapshot();
  };

  const handleEnterRuntimeDiagnostics = () => {
    setActiveRoleView('diagnostics');
  };

  const handleBackToMain = () => {
    setActiveRoleView('main');
  };

  if (activeRoleView === 'chief_engineer') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <ChiefEngineerWorkspace
          workspace={workspace}
          engineStatus={engineStatus}
          tasks={directorWorkspaceTasks}
          workers={runtimeWorkers}
          pmState={snapshot?.pm_state ?? null}
          directorRunning={directorRunning}
          isStartingDirector={isStartingDirector}
          onBackToMain={handleBackToMain}
          onEnterDirectorWorkspace={handleEnterDirectorWorkspace}
          onToggleDirector={() => toggleDirector(directorRunning, {
            required: agentsRequired,
            draftReady: agentsDraftReady,
          }, directorSeedTasks)}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  // Render Director Workspace
  if (activeRoleView === 'director') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <DirectorWorkspace
          workspace={workspace}
          onBackToMain={handleBackToMain}
          tasks={directorWorkspaceTasks}
          workers={runtimeWorkers}
          directorRunning={directorRunning}
          isStarting={isStartingDirector}
          onToggleDirector={() => toggleDirector(directorRunning, {
            required: agentsRequired,
            draftReady: agentsDraftReady,
          }, directorSeedTasks)}
          currentTaskId={engineStatus?.roles?.Director?.task_id ?? null}
          currentTaskTitle={engineStatus?.roles?.Director?.task_title ?? null}
          currentTaskStatus={engineStatus?.roles?.Director?.status ?? null}
          fileEditEvents={fileEditEvents}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          currentPhase={effectiveCurrentPhase}
          taskProgressMap={taskProgressMap}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  // Render PM Workspace
  if (activeRoleView === 'pm') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <PMWorkspace
          tasks={pmTasks}
          pmState={snapshot?.pm_state ?? null}
          pmRunning={effectivePmRunning}
          pmTerminalStatus={pmStatus}
          pmStartBlockedReason={pmStartBlockedReason}
          runtimeIssue={activeRuntimeIssue}
          isStarting={isStartingPM}
          onBackToMain={handleBackToMain}
          onTogglePm={() => togglePm(rawPmRunning)}
          onRunPmOnce={runPmOnce}
          workspace={workspace}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          taskTraceMap={taskTraceMap}
          onOpenSettings={() => uiActions.openSettings()}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  // Render Factory Workspace
  if (activeRoleView === 'factory') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <FactoryWorkspace
          workspace={workspace}
          onBackToMain={handleBackToMain}
          tasks={liveSnapshotTasks}
          pmTasks={pmTasks}
          directorTasks={directorTasks}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
          currentRun={factoryCurrentRun}
          events={factoryEvents}
          artifacts={factoryArtifacts}
          summaryMd={factorySummaryMd}
          summaryJson={factorySummaryJson}
          artifactsError={factoryArtifactsError}
          isArtifactsLoading={factoryArtifactsLoading}
          onStart={() => startFactoryRun({ workspace, run_director: true })}
          onCancel={() => factoryCurrentRun && stopFactoryRun(factoryCurrentRun.run_id)}
          isLoading={factoryIsLoading}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  if (activeRoleView === 'agi') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <ResidentWorkspace
          workspace={workspace}
          onBackToMain={handleBackToMain}
          residentSnapshot={displaySnapshot?.resident ?? snapshot?.resident ?? null}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  if (activeRoleView === 'diagnostics') {
    return (
      <ErrorBoundaryClass onError={(error) => {
        notifyError(error.message || '发生未知错误');
      }}>
        <RuntimeDiagnosticsWorkspace
          workspace={workspace}
          connectionState={{
            live,
            reconnecting,
            attemptCount,
          }}
          onBackToMain={handleBackToMain}
        />
        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />
        <Toaster position="bottom-right" />
      </ErrorBoundaryClass>
    );
  }

  // Render Main View (default)
  return (
    <ErrorBoundaryClass onError={(error) => {
      notifyError(error.message || '发生未知错误');
    }}>
      <div className="size-full flex flex-col bg-bg text-text-main font-sans overflow-hidden relative">
        <EnhancedNotificationManager
          notifications={notifications}
          onDismiss={removeNotification}
          maxVisible={5}
        />

        <ControlPanel
          workspace={workspace}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          pmToggleDisabled={(lancedbBlocked || docsMissing || llmPmBlocked) && !rawPmRunning}
          directorToggleDisabled={
            ((lancedbBlocked || docsMissing) && !directorRunning) ||
            (agentsRequired && !directorRunning) ||
            (llmDirectorBlocked && !directorRunning)
          }
          directorBlockedReason={
            docsMissing && !directorRunning
              ? 'docs/ missing'
              : agentsRequired && !directorRunning
                ? agentsDraftFailed ? 'AGENTS 草稿生成失败' : '需要先确认 AGENTS.md'
                : llmDirectorBlocked && !directorRunning
                  ? llmDirectorBlockedReason
                  : ''
          }
          runOnceDisabled={rawPmRunning || directorRunning || llmPmBlocked}
          onOpenSettings={() => uiActions.openSettings()}
          onPickWorkspace={handlePickWorkspace}
          onTogglePm={() => togglePm(rawPmRunning)}
          onRunPmOnce={runPmOnce}
          onResumePm={() => { }}
          onToggleDirector={() => toggleDirector(directorRunning, {
            required: agentsRequired,
            draftReady: agentsDraftReady,
          }, directorSeedTasks)}
          onRefresh={handleRefresh}
          onOpenBrain={() => { }}
          agentsNeeded={agentsRequired}
          agentsDraftReady={agentsDraftReady}
          agentsDraftFailed={agentsDraftFailed}
          onOpenAgentsReview={() => uiActions.openAgentsDialog()}
          onGenerateAgentsDraft={runPmOnce}
          isStartingPM={isStartingPM}
          isStoppingPM={isStoppingPM}
          isStartingDirector={isStartingDirector}
          isStoppingDirector={isStoppingDirector}
          onPingHealth={async () => { }}
          onOpenLogs={() => uiActions.openLogs('pm-subprocess')}
          isArtifactsOpen={ui.isMonitorOpen}
          onToggleArtifacts={() => uiActions.toggleMonitor()}
          onToggleTerminal={uiActions.toggleTerminal}
          isTerminalOpen={ui.showTerminal}
          onEnterPMWorkspace={handleEnterPMWorkspace}
          onEnterChiefEngineerWorkspace={handleEnterChiefEngineerWorkspace}
          onEnterDirectorWorkspace={handleEnterDirectorWorkspace}
          onEnterFactoryMode={handleEnterFactoryMode}
          onEnterAGIWorkspace={handleEnterAGIWorkspace}
          onEnterRuntimeDiagnostics={handleEnterRuntimeDiagnostics}
          // 新增：即时反馈状态
          currentPhase={effectiveCurrentPhase}
          currentTask={engineStatus?.roles?.Director?.task_title ?? undefined}
          isExecutingTool={Boolean(latestProcessActivity)}
          currentToolName={latestProcessActivity?.message}
        />

        <RealTimeStatusBar
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          pmStartedAt={normalizeStartedAtSeconds(pmStatus?.started_at)}
          directorStartedAt={normalizeStartedAtSeconds(directorStatus?.started_at)}
          pmIteration={toIterationValue(displaySnapshot)}
          llmStatus={llmStatusForBar}
          lancedbOk={lancedbStatus?.ok}
          fileEditEvents={fileEditEvents}
        />

        <LlmRuntimeOverlay
          activeView={activeRoleView}
          websocketLive={live}
          websocketReconnecting={reconnecting}
          websocketAttemptCount={attemptCount}
          pmRunning={effectivePmRunning}
          directorRunning={directorRunning}
          llmState={llmRuntimeState.state}
          llmBlockedRoles={llmRuntimeState.blockedRoles}
          llmRequiredRoles={llmRuntimeState.requiredRoles}
          llmLastUpdated={llmRuntimeState.lastUpdated}
          currentPhase={effectiveCurrentPhase}
          qualityGate={qualityGate}
          executionLogs={executionLogs}
          llmStreamEvents={llmStreamEvents}
          processStreamEvents={processStreamEvents}
          fileEditEvents={fileEditEvents}
        />

        <PanelGroup direction="horizontal" autoSaveId="polaris-main-layout-v2" className="flex-1 flex overflow-hidden">
          {ui.isMonitorOpen && (
            <>
              <Panel defaultSize={20} minSize={15} maxSize={30} order={1}>
                <div className="size-full border-r border-white/10 bg-bg-panel/30 backdrop-blur-md flex flex-col">
                  <Suspense fallback={<div className="flex items-center justify-center h-full text-text-dim">加载中...</div>}>
                    <ProcessMonitorSidebar
                      onFileSelect={fileManager.selectFile}
                      selectedFileId={fileManager.selectedFile?.id || null}
                      onOpenWorkspace={handleOpenWorkspace}
                      onOpenHistory={() => uiActions.openHistoryDrawer()}
                      fileStatusLines={snapshot?.file_status ?? null}
                      usageStats={usageStats}
                      usageLoading={usageLoading}
                      usageError={usageError}
                      onRefreshUsage={refreshUsage}
                    />
                  </Suspense>
                </div>
              </Panel>
              <PanelResizeHandle className="w-1 bg-white/5 hover:bg-accent transition-colors" />
            </>
          )}

          <Panel order={2} className="flex flex-col min-w-0">
            <PanelGroup direction="vertical">
              <Panel ref={workspacePanelRef} minSize={30} collapsible>
                <div className="flex flex-col h-full">
                  <div className="flex items-center justify-between border-b border-white/10 bg-bg-panel/20 px-4">
                    <div className="flex items-center gap-3 py-2">
                      <span className="text-sm font-heading font-bold text-text-main">当前批次主战场</span>
                    </div>
                    <button
                      onClick={() => uiActions.openHistoryDrawer()}
                      className="px-3 py-1.5 text-xs font-medium text-text-dim hover:text-text-main border border-white/10 hover:border-accent/30 rounded-lg transition-colors"
                    >
                      案卷历史
                    </button>
                  </div>

                  <div className="flex-1 min-h-0 overflow-hidden">
                    <ProjectProgressPanel
                      tasks={progressPmTasks}
                      directorTasks={progressDirectorTasks}
                      directorTaskSource={progressDirectorTaskSource}
                      directorRealtimeConnected={isProgressDirectorRealtimeConnected}
                      pmState={displaySnapshot?.pm_state ?? null}
                      focus={displaySnapshot?.focus ?? null}
                      notes={displaySnapshot?.notes ?? null}
                      goals={displaySnapshot?.goals ?? null}
                      planText={displaySnapshot?.plan_text ?? null}
                      planMtime={displaySnapshot?.plan_mtime ?? null}
                      planTextNormalized={displaySnapshot?.plan_text_normalized ?? false}
                      pmRunning={effectivePmRunning}
                      engineStatus={engineStatus}
                      onOpenDocsPanel={() => uiActions.openDocsInit()}
                      className="h-full"
                      // 新增：详细状态
                      qualityGate={qualityGate}
                      executionLogs={executionLogs}
                      currentPhase={effectiveCurrentPhase}
                    />
                  </div>
                </div>
              </Panel>

              {ui.showTerminal && (
                <>
                  <PanelResizeHandle className="h-1 bg-white/5 hover:bg-accent transition-colors" />
                  <Suspense fallback={null}>
                    <Panel ref={terminalPanelRef} defaultSize={30} minSize={10} maxSize={80} collapsible>
                      <TerminalPanel
                        isVisible={ui.showTerminal}
                        onClose={() => uiActions.setShowTerminal(false)}
                        workspacePath={workspace}
                        isMaximized={ui.isTerminalMaximized}
                        onToggleMaximize={toggleTerminalMaximize}
                        isResettingTasks={false}
                      />
                    </Panel>
                  </Suspense>
                </>
              )}
            </PanelGroup>
          </Panel>

          <PanelResizeHandle className="w-1 bg-white/5 hover:bg-accent transition-colors" />

          <Panel defaultSize={30} minSize={20} maxSize={50} order={3}>
            <ContextSidebar
              dialogueEvents={dialogueEvents}
              live={live}
              dialogueLoading={!live && dialogueEvents.length === 0}
              onClearDialogueLogs={() => { }}
              clearingDialogueLogs={false}
              memoItems={memos.memoItems}
              memoSelected={memos.memoSelected}
              memoContent={memos.memoData.content}
              memoMtime={memos.memoData.mtime}
              memoLoading={memos.memoLoading}
              memoError={memos.memoError}
              onSelectMemo={memos.selectMemo}
              memoryContent={memory.memoryData.content}
              memoryMtime={memory.memoryData.mtime}
              memoryLoading={memory.memoryLoading}
              memoryError={memory.memoryError}
              showCognition={ui.showCognition}
              setShowCognition={(v) => { }}
              settingsShowMemory={!!settings?.show_memory}
              anthroState={anthroState}
              snapshotTimestamp={displaySnapshot?.timestamp ?? null}
              snapshotFileStatus={displaySnapshot?.file_status ?? null}
              snapshotFilePaths={displaySnapshot?.file_paths ?? null}
              snapshotDirectorState={displaySnapshot?.director_state ?? null}
              resident={displaySnapshot?.resident ?? null}
            />
          </Panel>
        </PanelGroup>

        <Suspense fallback={null}>
          <SettingsModal
            isOpen={ui.isSettingsOpen}
            initialTab={ui.settingsInitialTab}
            onClose={() => uiActions.closeSettings()}
            onLlmStatusChange={() => { }}
            settings={settings}
            onSave={handleSaveSettings}
          />
        </Suspense>

        <Suspense fallback={null}>
          <DocsInitDialog
            open={ui.isDocsInitOpen}
            onOpenChange={(open) => open ? uiActions.openDocsInit() : uiActions.closeDocsInit()}
            workspace={workspace}
            workspaceStatus={snapshot?.workspace_status}
            docsPresent={snapshot?.docs_present}
            onApplied={() => {
              uiActions.closeDocsInit();
              handleRefresh();
            }}
          />
        </Suspense>

        <Suspense fallback={null}>
          <LogsModal
            isOpen={ui.isLogsOpen}
            onClose={() => uiActions.closeLogs()}
            initialSourceId={ui.logsSourceId}
            banner={ui.logsBanner}
            onDismissBanner={() => { }}
          />
        </Suspense>

        <AgentsReviewDialog
          open={ui.isAgentsDialogOpen}
          onOpenChange={(open) => open ? uiActions.openAgentsDialog() : uiActions.closeAgentsDialog()}
          agentsDraftFailed={agentsDraftFailed}
          agentsReview={snapshot?.agents_review ?? null}
          onOpenLogs={() => uiActions.openLogs('pm-subprocess')}
          onOpenDraft={() => {
            if (snapshot?.agents_review?.draft_path) {
              fileManager.selectFile({
                id: 'agents-draft',
                name: 'AGENTS.generated.md',
                path: snapshot.agents_review.draft_path,
              });
              uiActions.closeAgentsDialog();
            }
          }}
          workspace={workspace}
          agentsDraftMtime={agentsReview.draftMtime}
          agentsFeedbackSavedAt={agentsReview.feedbackSavedAt}
          agentsLoading={agentsReview.loading}
          agentsDraftContent={agentsReview.draftContent}
          agentsFeedback={agentsReview.feedback}
          onAgentsFeedbackChange={agentsReview.updateFeedback}
          onRetryGenerate={runPmOnce}
          onSubmitFeedback={agentsReview.saveFeedback}
          onApplyDraft={agentsReview.applyDraft}
          agentsApplying={agentsReview.applying}
        />

        <RuntimeErrorDialog
          open={ui.isRuntimeDialogOpen}
          issue={activeRuntimeIssue}
          onOpenChange={(open) => open ? uiActions.openRuntimeDialog() : uiActions.closeRuntimeDialog()}
          onOpenLogs={handleRuntimeOpenLogs}
          onDismiss={handleRuntimeDismiss}
        />

        <HistoryDrawer
          open={ui.isHistoryDrawerOpen}
          onOpenChange={(open) => open ? uiActions.openHistoryDrawer() : uiActions.closeHistoryDrawer()}
        />

        <Toaster position="bottom-right" />
      </div>
    </ErrorBoundaryClass>
  );
}

// App wrapper with RuntimeTransportProvider for unified WebSocket management
export default function App(): React.ReactElement {
  // Global unhandled promise rejection handler
  useEffect(() => {
    const handler = (event: PromiseRejectionEvent) => {
      devLogger.error('[Unhandled Promise Rejection]:', event.reason);
    };
    window.addEventListener('unhandledrejection', handler);
    return () => window.removeEventListener('unhandledrejection', handler);
  }, []);

  return (
    <RuntimeTransportProvider autoConnect>
      <AppContent />
    </RuntimeTransportProvider>
  );
}
