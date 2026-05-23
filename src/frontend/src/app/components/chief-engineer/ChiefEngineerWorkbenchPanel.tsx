import { useCallback, useEffect, useMemo, useState } from 'react';
import { GitBranch, Loader2, PlusCircle, UploadCloud, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { AIDialoguePanel, type AIDialoguePanelProps } from '@/app/components/ai-dialogue';
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

  const loadDirectorRunEvidence = useCallback(async (runId: string) => {
    setDirectorRunEvidence({
      runId,
      loading: true,
      data: null,
      error: null,
    });
    setDirectorRunCancelEvidence({
      runId,
      loading: false,
      message: null,
      error: null,
    });

    const result = await getDirectorRun(runId);
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
  }, []);

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
      const result = await cancelDirectorRun(runId);
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
  }, [directorRunEvidence.runId]);

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

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-cyan-500/20 bg-cyan-500/5 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-medium text-cyan-100">Chief Engineer 工作台</span>
          {sessionId && (
            <span className="rounded border border-cyan-500/20 bg-cyan-500/10 px-2 py-0.5 font-mono text-xs text-cyan-200">
              {sessionId.slice(0, 8)}...
            </span>
          )}
          {selectableSessions.length > 0 && (
            <select
              aria-label="选择 Chief Engineer RoleSession"
              data-testid="chief-engineer-role-session-select"
              value={sessionId ?? ''}
              onChange={(event) => handleSessionChange(event.target.value || null)}
              className="h-7 max-w-48 rounded border border-cyan-500/20 bg-slate-950/80 px-2 text-xs text-cyan-100 outline-none transition-colors hover:border-cyan-500/40 focus:border-cyan-400"
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
            className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border border-cyan-500/25 bg-cyan-500/15 px-2 text-xs text-cyan-100 transition-colors hover:bg-cyan-500/25"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            新建会话
          </button>
          {sessionId && (
            <button
              type="button"
              onClick={handleExportToDirector}
              className="inline-flex h-7 cursor-pointer items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/15 px-2 text-xs text-emerald-100 transition-colors hover:bg-emerald-500/25"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              导出 Director
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-cyan-500/15 bg-slate-950/60 px-4 py-2 text-[11px]">
        <MetricPill label="PM tasks" value={taskCount} />
        <MetricPill label="blueprints" value={blueprintCount} />
        <MetricPill label="missing" value={missingBlueprintCount} tone={missingBlueprintCount > 0 ? 'amber' : 'cyan'} />
        <MetricPill label="Director" value={directorRunning ? 'running' : 'idle'} tone={directorRunning ? 'emerald' : 'slate'} />
      </div>

      {directorRunEvidence.runId && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 border-b border-cyan-500/15 bg-slate-950/70 px-4 py-2 text-[11px]"
          data-testid="chief-engineer-workbench-run-evidence"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-cyan-200/80">/v2/director/runs/{directorRunEvidence.runId}</span>
            <span className="text-slate-300">
              {directorRunEvidence.loading
                ? '正在读取运行快照...'
                : directorRunEvidence.error
                  ? directorRunEvidence.error
                  : `${directorRunEvidence.data?.status || 'unknown'} · queued=${directorRunEvidence.data?.tasks_queued ?? 0}`}
            </span>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                void handleCancelDirectorRun();
              }}
              data-testid="chief-engineer-workbench-run-cancel"
              disabled={cancelDirectorRunDisabled}
              className="inline-flex h-6 cursor-pointer items-center gap-1 rounded border border-rose-500/20 bg-rose-500/10 px-2 text-[11px] text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500"
            >
              {directorRunCancelEvidence.loading ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <XCircle className="h-3 w-3" />
              )}
              取消
            </button>
            {directorRunCancelEvidence.runId === directorRunEvidence.runId &&
            (directorRunCancelEvidence.loading || directorRunCancelEvidence.message || directorRunCancelEvidence.error) ? (
              <span
                className={directorRunCancelEvidence.error ? 'font-mono text-rose-300' : 'font-mono text-cyan-200/80'}
                data-testid="chief-engineer-workbench-run-cancel-result"
              >
                /v2/director/runs/{directorRunEvidence.runId}/cancel ·{' '}
                {directorRunCancelEvidence.loading
                  ? 'cancelling'
                  : directorRunCancelEvidence.error || directorRunCancelEvidence.message}
              </span>
            ) : null}
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}

function MetricPill({
  label,
  value,
  tone = 'cyan',
}: {
  label: string;
  value: string | number;
  tone?: 'cyan' | 'emerald' | 'amber' | 'slate';
}) {
  const toneClass = tone === 'emerald'
    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
    : tone === 'amber'
      ? 'border-amber-500/25 bg-amber-500/10 text-amber-200'
      : tone === 'slate'
        ? 'border-white/10 bg-white/5 text-slate-300'
        : 'border-cyan-500/25 bg-cyan-500/10 text-cyan-200';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 ${toneClass}`}>
      <GitBranch className="h-3 w-3" />
      <span className="text-slate-500">{label}</span>
      <span className="font-mono">{value}</span>
    </span>
  );
}
