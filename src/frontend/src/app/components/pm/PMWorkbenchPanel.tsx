import { useCallback, useEffect, useMemo, useState } from 'react';
import { GitBranch, Loader2, Play, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { AIDialoguePanel, type AIDialoguePanelProps } from '@/app/components/ai-dialogue';
import { devLogger } from '@/app/utils/devLogger';
import {
  createRoleSession,
  exportRoleSessionToWorkflow,
  listRoleSessions,
  type RoleSessionListItem,
} from '@/services/roleSessionService';
import {
  cancelPmRun,
  getPmRun,
  runPm,
  type PmOrchestrationRunResponse,
  type RunPmPayload,
} from '@/services/pmService';

interface PMWorkbenchPanelProps {
  pmRunning?: boolean;
  workspace?: string;
  taskCount?: number;
  /** 初始 Session ID */
  initialSessionId?: string;
  /** 宿主类型，默认 electron_workbench */
  hostKind?: 'workflow' | 'electron_workbench' | 'tui' | 'cli' | 'api_server' | 'headless';
  /** 附着模式 */
  attachmentMode?: 'isolated' | 'attached_readonly' | 'attached_collaborative';
  /** 附着的工作流 Run ID */
  attachedRunId?: string;
  /** 附着的任务 ID */
  attachedTaskId?: string;
}

interface PMWorkflowRunEvidence {
  runId: string | null;
  loading: boolean;
  data: PmOrchestrationRunResponse | null;
  error: string | null;
}

interface PMWorkflowRunCancelEvidence {
  runId: string | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

const TERMINAL_PM_RUN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'blocked', 'timeout']);

function isTerminalPmRunStatus(status?: string | null): boolean {
  return TERMINAL_PM_RUN_STATUSES.has(String(status || '').trim().toLowerCase());
}

/**
 * PM Workbench Panel - PM 角色工作台
 *
 * 基于 AIDialoguePanel，预配置为 PM 角色。
 * 支持 RoleSession 多宿主架构，可创建独立的工作台会话。
 *
 * 与普通 PMAIDialoguePanel 的区别：
 * - 自动创建/管理 RoleSession
 * - 支持会话切换
 * - 支持导出到工作流
 */
export function PMWorkbenchPanel({
  pmRunning = false,
  workspace,
  taskCount = 0,
  initialSessionId,
  hostKind = 'electron_workbench',
  attachmentMode = 'isolated',
  attachedRunId,
  attachedTaskId,
}: PMWorkbenchPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [sessions, setSessions] = useState<RoleSessionListItem[]>([]);
  const [orchestrationDirective, setOrchestrationDirective] = useState('');
  const [orchestrationStage, setOrchestrationStage] = useState<NonNullable<RunPmPayload['stage']>>('pm');
  const [shouldRunDirector, setShouldRunDirector] = useState(false);
  const [isLaunchingOrchestration, setIsLaunchingOrchestration] = useState(false);
  const [workflowRunEvidence, setWorkflowRunEvidence] = useState<PMWorkflowRunEvidence>({
    runId: null,
    loading: false,
    data: null,
    error: null,
  });
  const [workflowRunCancelEvidence, setWorkflowRunCancelEvidence] = useState<PMWorkflowRunCancelEvidence>({
    runId: null,
    loading: false,
    message: null,
    error: null,
  });

  const loadSessions = useCallback(async () => {
    if (!workspace) return;

    const result = await listRoleSessions({
      role: 'pm',
      hostKind,
      workspace,
      limit: 20,
    });

    if (result.ok) {
      setSessions(result.data ?? []);
    } else {
      devLogger.error('[PMWorkbenchPanel] Failed to load sessions:', result.error);
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

  const loadPmRunEvidence = useCallback(async (runId: string) => {
    setWorkflowRunEvidence({
      runId,
      loading: true,
      data: null,
      error: null,
    });
    setWorkflowRunCancelEvidence({
      runId,
      loading: false,
      message: null,
      error: null,
    });
    const runResult = await getPmRun(runId);
    if (runResult.ok && runResult.data) {
      setWorkflowRunEvidence({
        runId,
        loading: false,
        data: runResult.data,
        error: null,
      });
    } else {
      setWorkflowRunEvidence({
        runId,
        loading: false,
        data: null,
        error: runResult.error || 'PM run detail unavailable',
      });
    }
  }, []);

  const handleCancelPmRun = useCallback(async () => {
    const runId = String(workflowRunEvidence.runId || '').trim();
    if (!runId) return;

    setWorkflowRunCancelEvidence({
      runId,
      loading: true,
      message: null,
      error: null,
    });

    try {
      const result = await cancelPmRun(runId);
      if (result.ok && result.data) {
        const status = String(result.data.status || 'unknown').trim() || 'unknown';
        setWorkflowRunEvidence({
          runId,
          loading: false,
          data: result.data,
          error: null,
        });
        setWorkflowRunCancelEvidence({
          runId,
          loading: false,
          message: `取消运行已提交: ${status}`,
          error: null,
        });
        toast.success('PM 编排取消已提交', {
          description: `Run ID: ${runId}`,
        });
      } else {
        const error = result.ok ? '后端未返回取消结果' : result.error;
        setWorkflowRunCancelEvidence({
          runId,
          loading: false,
          message: null,
          error: error || 'PM run cancel failed',
        });
        toast.error('PM 编排取消失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : '未知错误';
      setWorkflowRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error,
      });
      toast.error('PM 编排取消失败', {
        description: error,
      });
    }
  }, [workflowRunEvidence.runId]);

  const handleNewSession = async () => {
    try {
      const result = await createRoleSession({
        role: 'pm',
        host_kind: hostKind,
        workspace,
        attachment_mode: attachmentMode,
        context_config: {
          pm_running: pmRunning,
          task_count: taskCount,
        },
      });

      if (result.ok && result.data) {
        setSessionId(result.data.id);
        await loadSessions();
      } else {
        const error = result.ok ? 'RoleSession create response missing session' : result.error;
        devLogger.error('[PMWorkbenchPanel] Failed to create session:', error);
        toast.error('新建会话失败', {
          description: error,
        });
      }
    } catch (err) {
      devLogger.error('[PMWorkbenchPanel] Failed to create session:', err);
      toast.error('新建会话失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleExportToWorkflow = async () => {
    if (!sessionId) return;

    try {
      const result = await exportRoleSessionToWorkflow(sessionId, {
        target: 'pm',
        export_kind: 'session_bundle',
        include_audit_log: true,
      });

      if (result.ok && result.data?.run_id) {
        const runId = result.data.run_id;
        devLogger.debug('[PMWorkbenchPanel] Exported to workflow:', result.data);
        toast.success('已导出到 PM 工作流', {
          description: `Run ID: ${runId}\nArtifacts: ${result.data.artifact_count || 0}`,
        });
        await loadPmRunEvidence(runId);
      } else {
        const error = result.ok ? result.data?.error || '后端未返回 Run ID' : result.error;
        devLogger.error('[PMWorkbenchPanel] Export failed:', error);
        toast.error('导出失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[PMWorkbenchPanel] Failed to export:', err);
      toast.error('导出失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleRunPMOrchestration = async () => {
    if (!workspace) {
      toast.error('PM 编排启动失败', {
        description: '缺少 workspace',
      });
      return;
    }

    setIsLaunchingOrchestration(true);
    try {
      const payload: RunPmPayload = {
        workspace,
        directive: orchestrationDirective.trim(),
        stage: orchestrationStage,
        run_director: shouldRunDirector,
        director_iterations: 2,
        metadata: {
          source: 'pm_workbench',
          role_session_id: sessionId,
          host_kind: hostKind,
        },
      };
      const result = await runPm(payload);
      if (result.ok && result.data?.run_id) {
        const runId = result.data.run_id;
        toast.success('PM 编排已启动', {
          description: `Run ID: ${runId}`,
        });
        await loadPmRunEvidence(runId);
      } else {
        const error = result.ok ? '后端未返回 Run ID' : result.error;
        devLogger.error('[PMWorkbenchPanel] PM orchestration failed:', error);
        toast.error('PM 编排启动失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[PMWorkbenchPanel] Failed to run PM orchestration:', err);
      toast.error('PM 编排启动失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    } finally {
      setIsLaunchingOrchestration(false);
    }
  };

  const dialoguePanelProps: AIDialoguePanelProps = {
    dialogueRole: 'pm',
    roleDisplayName: 'PM',
    roleTheme: {
      primary: 'amber',
      secondary: 'amber-400',
      gradient: 'from-amber-500 to-amber-700',
    },
    welcomeMessage: 'PM 工作台已就绪。您可以创建任务计划、分析项目状态，或导出工作建议到正式流程。',
    context: {
      workspace,
      task_count: taskCount,
      pm_running: pmRunning,
    },
    workspace,
    sessionId: sessionId ?? undefined,
    hostKind,
    attachmentMode,
    attachedRunId,
    attachedTaskId,
    onSessionChange: handleSessionChange,
  };
  const cancelPmRunDisabled =
    !workflowRunEvidence.runId ||
    workflowRunEvidence.loading ||
    workflowRunCancelEvidence.loading ||
    isTerminalPmRunStatus(workflowRunEvidence.data?.status);

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-amber-500/20 bg-amber-500/5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-sm text-amber-600 font-medium">PM 工作台</span>
          {sessionId && (
            <span className="text-xs text-muted-foreground px-2 py-0.5 rounded bg-amber-500/10">
              {sessionId.slice(0, 8)}...
            </span>
          )}
          {selectableSessions.length > 0 && (
            <select
              aria-label="选择 PM RoleSession"
              data-testid="pm-role-session-select"
              value={sessionId ?? ''}
              onChange={(event) => handleSessionChange(event.target.value || null)}
              className="h-7 max-w-48 rounded border border-amber-500/20 bg-slate-950/80 px-2 text-xs text-amber-100 outline-none transition-colors hover:border-amber-500/40 focus:border-amber-400"
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
            onClick={handleNewSession}
            className="text-xs px-2 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-700 transition-colors"
          >
            新建会话
          </button>
          {sessionId && (
            <button
              onClick={handleExportToWorkflow}
              className="text-xs px-2 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-700 transition-colors"
            >
              导出到流程
            </button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-amber-500/15 bg-slate-950/60 px-4 py-2 text-xs">
        <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded border border-amber-500/20 bg-slate-950/70 px-2 py-1.5">
          <GitBranch className="h-3.5 w-3.5 shrink-0 text-amber-300/80" />
          <input
            value={orchestrationDirective}
            onChange={(event) => setOrchestrationDirective(event.target.value)}
            data-testid="pm-workbench-run-directive"
            placeholder="需求指令"
            className="min-w-0 flex-1 bg-transparent text-xs text-amber-50 placeholder:text-amber-200/35 outline-none"
          />
        </div>
        <select
          aria-label="PM 编排阶段"
          data-testid="pm-workbench-run-stage"
          value={orchestrationStage}
          onChange={(event) => setOrchestrationStage(event.target.value as NonNullable<RunPmPayload['stage']>)}
          className="h-8 rounded border border-amber-500/20 bg-slate-950/80 px-2 text-xs text-amber-100 outline-none transition-colors hover:border-amber-500/40 focus:border-amber-400"
        >
          <option value="pm">PM</option>
          <option value="architect">Architect</option>
        </select>
        <label className="flex h-8 items-center gap-1.5 rounded border border-amber-500/15 bg-amber-500/5 px-2 text-[11px] text-amber-100">
          <input
            type="checkbox"
            checked={shouldRunDirector}
            onChange={(event) => setShouldRunDirector(event.target.checked)}
            data-testid="pm-workbench-run-director"
            className="h-3.5 w-3.5 accent-amber-500"
          />
          Director
        </label>
        <button
          type="button"
          onClick={handleRunPMOrchestration}
          disabled={!workspace || isLaunchingOrchestration}
          data-testid="pm-workbench-run-pm"
          className="inline-flex h-8 items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/15 px-2.5 text-xs text-emerald-100 transition-colors hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLaunchingOrchestration ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
          编排
        </button>
      </div>

      {workflowRunEvidence.runId && (
        <div
          className="flex flex-wrap items-center justify-between gap-2 border-b border-amber-500/15 bg-slate-950/70 px-4 py-2 text-[11px]"
          data-testid="pm-workbench-run-evidence"
        >
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-amber-200/80">/v2/pm/runs/{workflowRunEvidence.runId}</span>
            <span className="text-slate-300">
              {workflowRunEvidence.loading
                ? '正在读取运行快照...'
                : workflowRunEvidence.error
                  ? workflowRunEvidence.error
                  : `${workflowRunEvidence.data?.status || 'unknown'} · ${workflowRunEvidence.data?.stage || 'stage unknown'}`}
            </span>
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                void handleCancelPmRun();
              }}
              data-testid="pm-workbench-run-cancel"
              disabled={cancelPmRunDisabled}
              className="inline-flex h-6 cursor-pointer items-center gap-1 rounded border border-rose-500/20 bg-rose-500/10 px-2 text-[11px] text-rose-200 transition-colors hover:bg-rose-500/20 disabled:cursor-not-allowed disabled:border-slate-600/20 disabled:bg-slate-700/20 disabled:text-slate-500"
            >
              {workflowRunCancelEvidence.loading ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <XCircle className="h-3 w-3" />
              )}
              取消
            </button>
            {workflowRunCancelEvidence.runId === workflowRunEvidence.runId &&
            (workflowRunCancelEvidence.loading || workflowRunCancelEvidence.message || workflowRunCancelEvidence.error) ? (
              <span
                className={workflowRunCancelEvidence.error ? 'font-mono text-rose-300' : 'font-mono text-amber-200/80'}
                data-testid="pm-workbench-run-cancel-result"
              >
                /v2/pm/runs/{workflowRunEvidence.runId}/cancel ·{' '}
                {workflowRunCancelEvidence.loading
                  ? 'cancelling'
                  : workflowRunCancelEvidence.error || workflowRunCancelEvidence.message}
              </span>
            ) : null}
          </div>
        </div>
      )}

      {/* 对话面板 */}
      <div className="flex-1 min-h-0">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}
