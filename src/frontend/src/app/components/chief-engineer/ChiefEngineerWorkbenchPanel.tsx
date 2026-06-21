import { useCallback, useEffect, useMemo, useState } from 'react';
import { GitBranch, PlusCircle, UploadCloud } from 'lucide-react';
import { toast } from 'sonner';
import { AIDialoguePanel, type AIDialoguePanelProps } from '@/app/components/ai-dialogue';
import { RoleFactoryRunEvidenceStrip } from '@/app/components/common/RoleFactoryRunEvidenceStrip';
import { RoleSessionEvidencePanel } from '@/app/components/common/RoleSessionEvidencePanel';
import { RoleRunEvidenceStrip } from '@/app/components/common/RoleRunEvidenceStrip';
import { ChiefEngineerGovernancePanel } from './ChiefEngineerGovernancePanel';
import { useRoleSessionFactoryExport } from '@/app/components/common/useRoleSessionFactoryExport';
import { devLogger } from '@/app/utils/devLogger';
import {
  createRoleSession,
  exportRoleSessionToWorkflow,
  listRoleSessions,
  type RoleSessionListItem,
} from '@/services/roleSessionService';
import { cancelDirectorRun, getDirectorRun, type DirectorOrchestrationRunResponse } from '@/services/pmService';

interface ChiefEngineerWorkbenchPanelProps {
  workspace?: string;
  taskCount?: number;
  blueprintCount?: number;
  missingBlueprintCount?: number;
  directorRunning?: boolean;
  initialSessionId?: string;
  hostKind?: 'workflow' | 'electron_workbench' | 'tui' | 'cli' | 'api_server' | 'headless';
  attachmentMode?: 'isolated' | 'attached_readonly' | 'attached_collaborative';
  attachedRunId?: string;
  attachedTaskId?: string;
}

interface DirectorRunEvidence {
  runId: string | null;
  loading: boolean;
  data: DirectorOrchestrationRunResponse | null;
  error: string | null;
}

interface DirectorRunCancelEvidence {
  runId: string | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

interface LoadDirectorRunEvidenceOptions {
  preserveData?: boolean;
  preserveCancel?: boolean;
}

const TERMINAL_DIRECTOR_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked', 'timeout']);

function isTerminalDirectorRunStatus(status?: string | null): boolean {
  return TERMINAL_DIRECTOR_RUN_STATUSES.has(String(status || '').trim().toLowerCase());
}

export function ChiefEngineerWorkbenchPanel({
  workspace,
  taskCount = 0,
  blueprintCount = 0,
  missingBlueprintCount = 0,
  directorRunning = false,
  initialSessionId,
  hostKind = 'electron_workbench',
  attachmentMode = 'isolated',
  attachedRunId,
  attachedTaskId,
}: ChiefEngineerWorkbenchPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [sessions, setSessions] = useState<RoleSessionListItem[]>([]);
  const [directorRunEvidence, setDirectorRunEvidence] = useState<DirectorRunEvidence>({
    runId: null,
    loading: false,
    data: null,
    error: null,
  });
  const [directorRunCancelEvidence, setDirectorRunCancelEvidence] = useState<DirectorRunCancelEvidence>({
    runId: null,
    loading: false,
    message: null,
    error: null,
  });
  const {
    isExportingFactory,
    factoryRunEvidence,
    factoryRunCancelEvidence,
    factoryRunRealtimePushActive,
    cancelFactoryRunDisabled,
    handleExportToFactory,
    handleRefreshFactoryRun,
    handleCancelFactoryRun,
  } = useRoleSessionFactoryExport({
    sessionId,
    logPrefix: 'ChiefEngineerWorkbenchPanel',
  });

  const loadSessions = useCallback(async () => {
    if (!workspace) return;

    const result = await listRoleSessions({
      role: 'chief_engineer',
      hostKind,
      workspace,
      limit: 20,
    });

    if (result.ok) {
      setSessions(result.data ?? []);
    } else {
      devLogger.error('[ChiefEngineerWorkbenchPanel] Failed to load sessions:', result.error);
    }
  }, [hostKind, workspace]);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const selectableSessions = useMemo(() => {
    if (!sessionId || sessions.some((session) => session.id === sessionId)) {
      return sessions;
    }
    return [{ id: sessionId, title: '当前会话' }, ...sessions];
  }, [sessionId, sessions]);

  const selectedSessionLabel = useMemo(() => {
    const current = selectableSessions.find((session) => session.id === sessionId);
    return current?.title || current?.id || '';
  }, [selectableSessions, sessionId]);

  const handleSessionChange = (newSessionId: string | null) => {
    setSessionId(newSessionId);
  };

  const loadDirectorRunEvidence = useCallback(async (runId: string, options: LoadDirectorRunEvidenceOptions = {}) => {
    setDirectorRunEvidence((current) => ({
      runId,
      loading: true,
      data: options.preserveData && current.runId === runId ? current.data : null,
      error: null,
    }));
    if (!options.preserveCancel) {
      setDirectorRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error: null,
      });
    }

    try {
      const result = await getDirectorRun(runId, workspace);
      if (result.ok && result.data) {
        setDirectorRunEvidence({
          runId,
          loading: false,
          data: result.data,
          error: null,
        });
        return;
      }
      setDirectorRunEvidence({
        runId,
        loading: false,
        data: null,
        error: result.error || 'Director run detail unavailable',
      });
    } catch (err) {
      setDirectorRunEvidence({
        runId,
        loading: false,
        data: null,
        error: err instanceof Error ? err.message : 'Director run detail unavailable',
      });
    }
  }, [workspace]);

  const handleRefreshDirectorRun = useCallback(() => {
    const runId = String(directorRunEvidence.runId || '').trim();
    if (!runId) return;
    void loadDirectorRunEvidence(runId, {
      preserveData: true,
      preserveCancel: true,
    });
  }, [directorRunEvidence.runId, loadDirectorRunEvidence]);

  const handleNewSession = async () => {
    try {
      const result = await createRoleSession({
        role: 'chief_engineer',
        host_kind: hostKind,
        workspace,
        attachment_mode: attachmentMode,
        context_config: {
          task_count: taskCount,
          blueprint_count: blueprintCount,
          missing_blueprint_count: missingBlueprintCount,
          director_running: directorRunning,
        },
      });

      if (result.ok && result.data) {
        setSessionId(result.data.id);
        await loadSessions();
      } else {
        const error = result.ok ? 'RoleSession create response missing session' : result.error;
        devLogger.error('[ChiefEngineerWorkbenchPanel] Failed to create session:', error);
        toast.error('新建会话失败', {
          description: error,
        });
      }
    } catch (err) {
      devLogger.error('[ChiefEngineerWorkbenchPanel] Failed to create session:', err);
      toast.error('新建会话失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleExportToDirector = async () => {
    if (!sessionId) return;

    try {
      const result = await exportRoleSessionToWorkflow(sessionId, {
        target: 'director',
        export_kind: 'session_bundle',
        include_audit_log: true,
      });

      if (result.ok && result.data?.run_id) {
        const runId = result.data.run_id;
        devLogger.debug('[ChiefEngineerWorkbenchPanel] Exported to Director workflow:', result.data);
        toast.success('已导出到 Director 工作流', {
          description: `Run ID: ${runId}\nArtifacts: ${result.data.artifact_count || 0}`,
        });
        await loadDirectorRunEvidence(runId);
      } else {
        const error = result.ok ? result.data?.error || '后端未返回 Run ID' : result.error;
        devLogger.error('[ChiefEngineerWorkbenchPanel] Export failed:', error);
        toast.error('导出失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[ChiefEngineerWorkbenchPanel] Failed to export:', err);
      toast.error('导出失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleCancelDirectorRun = useCallback(async () => {
    const runId = String(directorRunEvidence.runId || '').trim();
    if (!runId) return;

    setDirectorRunCancelEvidence({
      runId,
      loading: true,
      message: null,
      error: null,
    });

    try {
      const result = await cancelDirectorRun(runId, workspace);
      if (result.ok && result.data) {
        const status = String(result.data.status || 'unknown').trim() || 'unknown';
        setDirectorRunEvidence({
          runId,
          loading: false,
          data: result.data,
          error: null,
        });
        setDirectorRunCancelEvidence({
          runId,
          loading: false,
          message: `取消运行已提交: ${status}`,
          error: null,
        });
        toast.success('Director 编排取消已提交', {
          description: `Run ID: ${runId}`,
        });
      } else {
        const error = result.ok ? '后端未返回取消结果' : result.error;
        setDirectorRunCancelEvidence({
          runId,
          loading: false,
          message: null,
          error: error || 'Director run cancel failed',
        });
        toast.error('Director 编排取消失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : '未知错误';
      setDirectorRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error,
      });
      toast.error('Director 编排取消失败', {
        description: error,
      });
    }
  }, [directorRunEvidence.runId, workspace]);

  const dialoguePanelProps: AIDialoguePanelProps = {
    dialogueRole: 'chief_engineer',
    roleDisplayName: 'Chief Engineer',
    roleTheme: {
      primary: 'cyan',
      secondary: 'cyan-400',
      gradient: 'from-cyan-500 to-cyan-700',
    },
    welcomeMessage: 'Chief Engineer 工作台已就绪。您可以审查 PM 合同、生成施工蓝图，或把施工建议导出为 Director 执行流。',
    context: {
      workspace,
      task_count: taskCount,
      blueprint_count: blueprintCount,
      missing_blueprint_count: missingBlueprintCount,
      director_running: directorRunning,
    },
    workspace,
    sessionId: sessionId ?? undefined,
    hostKind,
    attachmentMode,
    attachedRunId,
    attachedTaskId,
    onSessionChange: handleSessionChange,
    workflowExportTarget: 'director',
    workflowExportLabel: '导出 Director',
  };

  const cancelDirectorRunDisabled =
    !directorRunEvidence.runId ||
    directorRunEvidence.loading ||
    directorRunCancelEvidence.loading ||
    isTerminalDirectorRunStatus(directorRunEvidence.data?.status);
  const directorRunRealtimePushActive = Boolean(directorRunEvidence.runId)
    && !directorRunEvidence.loading
    && !directorRunCancelEvidence.loading
    && !directorRunEvidence.error
    && !isTerminalDirectorRunStatus(directorRunEvidence.data?.status);

  return (
    <div data-testid="chief-engineer-workbench-panel" className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] bg-slate-950/80 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-medium text-slate-200">Chief Engineer 工作台</span>
          {sessionId && (
            <span className="soft-chip rounded px-2 py-0.5 font-mono text-xs text-slate-300">
              {sessionId.slice(0, 8)}...
            </span>
          )}
          {selectableSessions.length > 0 && (
            <select
              aria-label="选择 Chief Engineer RoleSession"
              data-testid="chief-engineer-role-session-select"
              value={sessionId ?? ''}
              onChange={(event) => handleSessionChange(event.target.value || null)}
              className="h-7 max-w-48 rounded border border-white/[0.08] bg-slate-950/80 px-2 text-xs text-slate-200 outline-none transition-colors hover:border-white/[0.14] focus:border-white/[0.18]"
              title={selectedSessionLabel || '选择会话'}
            >
              <option value="">选择会话</option>
              {selectableSessions.map((session) => (
                <option key={session.id} value={session.id}>
                  {session.title || session.id}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={handleNewSession}
            className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.04] px-2 text-xs text-slate-200 transition-colors hover:bg-white/[0.08]"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            新建会话
          </button>
          {sessionId && (
            <button
              type="button"
              onClick={handleExportToDirector}
              className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/[0.15] px-2 text-xs text-emerald-100 transition-colors hover:bg-emerald-500/25"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              导出 Director
            </button>
          )}
          {sessionId && (
            <button
              type="button"
              onClick={handleExportToFactory}
              disabled={isExportingFactory}
              className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.04] px-2 text-xs text-slate-200 transition-colors hover:bg-white/[0.08] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              导出 Factory
            </button>
          )}
        </div>
      </div>

      <RoleSessionEvidencePanel sessionId={sessionId} tone="cyan" />

      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] bg-slate-950/60 px-4 py-2 text-[11px]">
        <MetricPill label="PM tasks" value={taskCount} />
        <MetricPill label="blueprints" value={blueprintCount} />
        <MetricPill label="missing" value={missingBlueprintCount} tone={missingBlueprintCount > 0 ? 'amber' : 'slate'} />
        <MetricPill label="Director" value={directorRunning ? 'running' : 'idle'} tone={directorRunning ? 'emerald' : 'slate'} />
      </div>

      {directorRunEvidence.runId && (
        <RoleRunEvidenceStrip
          tone="cyan"
          testId="chief-engineer-workbench-run-evidence"
          endpoint={`/v2/director/runs/${directorRunEvidence.runId}`}
          workspace={workspace}
          loading={directorRunEvidence.loading}
          error={directorRunEvidence.error}
          status={directorRunEvidence.data?.status}
          details={[`queued=${directorRunEvidence.data?.tasks_queued ?? 0}`]}
          message={directorRunEvidence.data?.message}
          refreshTestId="chief-engineer-workbench-run-refresh"
          refreshDisabled={!directorRunEvidence.runId || directorRunEvidence.loading}
          refreshLoading={directorRunEvidence.loading}
          realtimePushActive={directorRunRealtimePushActive}
          onRefresh={handleRefreshDirectorRun}
          cancelTestId="chief-engineer-workbench-run-cancel"
          cancelDisabled={cancelDirectorRunDisabled}
          cancelLoading={directorRunCancelEvidence.loading}
          onCancel={() => { void handleCancelDirectorRun(); }}
          cancelResultTestId="chief-engineer-workbench-run-cancel-result"
          cancelResultEndpoint={`/v2/director/runs/${directorRunEvidence.runId}/cancel`}
          cancelResultVisible={
            directorRunCancelEvidence.runId === directorRunEvidence.runId
            && (directorRunCancelEvidence.loading || Boolean(directorRunCancelEvidence.message) || Boolean(directorRunCancelEvidence.error))
          }
          cancelResultLoading={directorRunCancelEvidence.loading}
          cancelResultMessage={directorRunCancelEvidence.message}
          cancelResultError={directorRunCancelEvidence.error}
        />
      )}

      <RoleFactoryRunEvidenceStrip
        tone="cyan"
        testId="chief-engineer-workbench-factory-evidence"
        runEvidence={factoryRunEvidence}
        cancelEvidence={factoryRunCancelEvidence}
        realtimePushActive={factoryRunRealtimePushActive}
        cancelDisabled={cancelFactoryRunDisabled}
        onRefresh={handleRefreshFactoryRun}
        onCancel={() => { void handleCancelFactoryRun(); }}
      />

      {workspace ? <ChiefEngineerGovernancePanel workspace={workspace} /> : null}

      <div className="min-h-0 flex-1">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}

function MetricPill({
  label,
  value,
  tone = 'slate',
}: {
  label: string;
  value: string | number;
  tone?: 'slate' | 'emerald' | 'amber';
}) {
  const toneClass = tone === 'emerald'
    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
    : tone === 'amber'
      ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
      : tone === 'slate'
        ? 'border-white/10 bg-white/5 text-slate-300'
        : 'border-white/[0.08] bg-white/[0.04] text-slate-300';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 ${toneClass}`}>
      <GitBranch className="h-3 w-3" />
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </span>
  );
}
