import { useCallback, useEffect, useMemo, useState } from 'react';
import { PlusCircle, UploadCloud } from 'lucide-react';
import { toast } from 'sonner';
import { AIDialoguePanel, type AIDialoguePanelProps } from '@/app/components/ai-dialogue';
import { RoleFactoryRunEvidenceStrip } from '@/app/components/common/RoleFactoryRunEvidenceStrip';
import { RoleSessionEvidencePanel } from '@/app/components/common/RoleSessionEvidencePanel';
import { RoleRunEvidenceStrip } from '@/app/components/common/RoleRunEvidenceStrip';
import { useRoleSessionFactoryExport } from '@/app/components/common/useRoleSessionFactoryExport';
import { devLogger } from '@/app/utils/devLogger';
import {
  createRoleSession,
  exportRoleSessionToWorkflow,
  listRoleSessions,
  type RoleSessionListItem,
} from '@/services/roleSessionService';
import { cancelDirectorRun, getDirectorRun, type DirectorOrchestrationRunResponse } from '@/services/pmService';

interface DirectorWorkbenchPanelProps {
  workspace?: string;
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
  /** 任务数量 */
  tasksCount?: number;
  /** 正在运行的任务 */
  runningTasks?: number;
}

interface DirectorWorkflowRunEvidence {
  runId: string | null;
  loading: boolean;
  data: DirectorOrchestrationRunResponse | null;
  error: string | null;
}

interface DirectorWorkflowRunCancelEvidence {
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

/**
 * Director Workbench Panel - Director 角色工作台
 *
 * 基于 AIDialoguePanel，预配置为 Director 角色。
 * 支持 RoleSession 多宿主架构，可创建独立的编码工作台会话。
 *
 * 特性：
 * - 完整的代码读写能力
 * - 命令执行能力
 * - 可导出补丁到工作流
 */
export function DirectorWorkbenchPanel({
  workspace,
  initialSessionId,
  hostKind = 'electron_workbench',
  attachmentMode = 'isolated',
  attachedRunId,
  attachedTaskId,
  tasksCount = 0,
  runningTasks = 0,
}: DirectorWorkbenchPanelProps) {
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [sessions, setSessions] = useState<RoleSessionListItem[]>([]);
  const [workflowRunEvidence, setWorkflowRunEvidence] = useState<DirectorWorkflowRunEvidence>({
    runId: null,
    loading: false,
    data: null,
    error: null,
  });
  const [workflowRunCancelEvidence, setWorkflowRunCancelEvidence] = useState<DirectorWorkflowRunCancelEvidence>({
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
    logPrefix: 'DirectorWorkbenchPanel',
  });

  const loadSessions = useCallback(async () => {
    if (!workspace) return;

    const result = await listRoleSessions({
      role: 'director',
      hostKind,
      workspace,
      limit: 20,
    });

    if (result.ok) {
      setSessions(result.data ?? []);
    } else {
      devLogger.error('[DirectorWorkbenchPanel] Failed to load sessions:', result.error);
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
    setWorkflowRunEvidence((current) => ({
      runId,
      loading: true,
      data: options.preserveData && current.runId === runId ? current.data : null,
      error: null,
    }));
    if (!options.preserveCancel) {
      setWorkflowRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error: null,
      });
    }

    try {
      const runResult = await getDirectorRun(runId, workspace);
      if (runResult.ok && runResult.data) {
        setWorkflowRunEvidence({
          runId,
          loading: false,
          data: runResult.data,
          error: null,
        });
        return;
      }
      setWorkflowRunEvidence({
        runId,
        loading: false,
        data: null,
        error: runResult.error || 'Director run detail unavailable',
      });
    } catch (err) {
      setWorkflowRunEvidence({
        runId,
        loading: false,
        data: null,
        error: err instanceof Error ? err.message : 'Director run detail unavailable',
      });
    }
  }, [workspace]);

  const handleRefreshDirectorRun = useCallback(() => {
    const runId = String(workflowRunEvidence.runId || '').trim();
    if (!runId) return;
    void loadDirectorRunEvidence(runId, {
      preserveData: true,
      preserveCancel: true,
    });
  }, [loadDirectorRunEvidence, workflowRunEvidence.runId]);

  const handleNewSession = async () => {
    try {
      const result = await createRoleSession({
        role: 'director',
        host_kind: hostKind,
        workspace,
        attachment_mode: attachmentMode,
        context_config: {
          tasks_count: tasksCount,
          running_tasks: runningTasks,
        },
      });

      if (result.ok && result.data) {
        setSessionId(result.data.id);
        await loadSessions();
      } else {
        const error = result.ok ? 'RoleSession create response missing session' : result.error;
        devLogger.error('[DirectorWorkbenchPanel] Failed to create session:', error);
        toast.error('新建会话失败', {
          description: error,
        });
      }
    } catch (err) {
      devLogger.error('[DirectorWorkbenchPanel] Failed to create session:', err);
      toast.error('新建会话失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleExportPatch = async () => {
    if (!sessionId) return;

    try {
      const result = await exportRoleSessionToWorkflow(sessionId, {
        target: 'director',
        export_kind: 'session_bundle',
        include_audit_log: true,
      });

      if (result.ok && result.data?.run_id) {
        const runId = result.data.run_id;
        devLogger.debug('[DirectorWorkbenchPanel] Exported to workflow:', result.data);
        toast.success('已导出到 Director 工作流', {
          description: `Run ID: ${runId}\nArtifacts: ${result.data.artifact_count || 0}`,
        });
        await loadDirectorRunEvidence(runId);
      } else {
        const error = result.ok ? result.data?.error || '后端未返回 Run ID' : result.error;
        devLogger.error('[DirectorWorkbenchPanel] Export failed:', error);
        toast.error('导出失败', {
          description: error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[DirectorWorkbenchPanel] Failed to export patch:', err);
      toast.error('导出失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const handleCancelDirectorRun = useCallback(async () => {
    const runId = String(workflowRunEvidence.runId || '').trim();
    if (!runId) return;

    setWorkflowRunCancelEvidence({
      runId,
      loading: true,
      message: null,
      error: null,
    });

    try {
      const result = await cancelDirectorRun(runId, workspace);
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
        toast.success('Director 编排取消已提交', {
          description: `Run ID: ${runId}`,
        });
      } else {
        const error = result.ok ? '后端未返回取消结果' : result.error;
        setWorkflowRunCancelEvidence({
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
      setWorkflowRunCancelEvidence({
        runId,
        loading: false,
        message: null,
        error,
      });
      toast.error('Director 编排取消失败', {
        description: error,
      });
    }
  }, [workflowRunEvidence.runId, workspace]);

  const dialoguePanelProps: AIDialoguePanelProps = {
    dialogueRole: 'director',
    roleDisplayName: 'Director',
    roleTheme: {
      primary: 'emerald',
      secondary: 'emerald-400',
      gradient: 'from-emerald-500 to-emerald-700',
    },
    welcomeMessage: 'Director 执行系统已就绪。您可以查看代码、编写文件、运行命令，或导出执行建议到正式流程。',
    context: {
      workspace,
      tasks_count: tasksCount,
      running_tasks: runningTasks,
    },
    workspace,
    sessionId: sessionId ?? undefined,
    hostKind,
    attachmentMode,
    attachedRunId,
    attachedTaskId,
    onSessionChange: handleSessionChange,
  };
  const cancelDirectorRunDisabled =
    !workflowRunEvidence.runId ||
    workflowRunEvidence.loading ||
    workflowRunCancelEvidence.loading ||
    isTerminalDirectorRunStatus(workflowRunEvidence.data?.status);
  const directorRunRealtimePushActive = Boolean(workflowRunEvidence.runId)
    && !workflowRunEvidence.loading
    && !workflowRunCancelEvidence.loading
    && !workflowRunEvidence.error
    && !isTerminalDirectorRunStatus(workflowRunEvidence.data?.status);

  return (
    <div data-testid="director-workbench-panel" className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-emerald-500/20 bg-emerald-500/5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="text-sm font-medium text-emerald-100">Director 工作台</span>
          {sessionId && (
            <span className="rounded border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 font-mono text-xs text-emerald-200">
              {sessionId.slice(0, 8)}...
            </span>
          )}
          {selectableSessions.length > 0 && (
            <select
              aria-label="选择 Director RoleSession"
              data-testid="director-role-session-select"
              value={sessionId ?? ''}
              onChange={(event) => handleSessionChange(event.target.value || null)}
              className="h-7 max-w-48 rounded border border-emerald-500/20 bg-slate-950/80 px-2 text-xs text-emerald-100 outline-none transition-colors hover:border-emerald-500/40 focus:border-emerald-400"
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
            className="inline-flex h-7 items-center gap-1.5 rounded border border-emerald-500/25 bg-emerald-500/[0.15] px-2 text-xs text-emerald-100 transition-colors hover:bg-emerald-500/25"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            新建会话
          </button>
          {sessionId && (
            <button
              type="button"
              onClick={handleExportPatch}
              className="inline-flex h-7 items-center gap-1.5 rounded border border-blue-500/25 bg-blue-500/[0.15] px-2 text-xs text-blue-100 transition-colors hover:bg-blue-500/25"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              导出补丁
            </button>
          )}
          {sessionId && (
            <button
              type="button"
              onClick={handleExportToFactory}
              disabled={isExportingFactory}
              className="inline-flex h-7 items-center gap-1.5 rounded border border-cyan-500/25 bg-cyan-500/[0.15] px-2 text-xs text-cyan-100 transition-colors hover:bg-cyan-500/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <UploadCloud className="h-3.5 w-3.5" />
              导出 Factory
            </button>
          )}
        </div>
      </div>

      <RoleSessionEvidencePanel sessionId={sessionId} tone="emerald" />

      {workflowRunEvidence.runId && (
        <RoleRunEvidenceStrip
          tone="emerald"
          testId="director-workbench-run-evidence"
          endpoint={`/v2/director/runs/${workflowRunEvidence.runId}`}
          workspace={workspace}
          loading={workflowRunEvidence.loading}
          error={workflowRunEvidence.error}
          status={workflowRunEvidence.data?.status}
          details={[`queued=${workflowRunEvidence.data?.tasks_queued ?? 0}`]}
          message={workflowRunEvidence.data?.message}
          refreshTestId="director-workbench-run-refresh"
          refreshDisabled={!workflowRunEvidence.runId || workflowRunEvidence.loading}
          refreshLoading={workflowRunEvidence.loading}
          realtimePushActive={directorRunRealtimePushActive}
          onRefresh={handleRefreshDirectorRun}
          cancelTestId="director-workbench-run-cancel"
          cancelDisabled={cancelDirectorRunDisabled}
          cancelLoading={workflowRunCancelEvidence.loading}
          onCancel={() => { void handleCancelDirectorRun(); }}
          cancelResultTestId="director-workbench-run-cancel-result"
          cancelResultEndpoint={`/v2/director/runs/${workflowRunEvidence.runId}/cancel`}
          cancelResultVisible={
            workflowRunCancelEvidence.runId === workflowRunEvidence.runId
            && (workflowRunCancelEvidence.loading || Boolean(workflowRunCancelEvidence.message) || Boolean(workflowRunCancelEvidence.error))
          }
          cancelResultLoading={workflowRunCancelEvidence.loading}
          cancelResultMessage={workflowRunCancelEvidence.message}
          cancelResultError={workflowRunCancelEvidence.error}
        />
      )}

      <RoleFactoryRunEvidenceStrip
        tone="emerald"
        testId="director-workbench-factory-evidence"
        runEvidence={factoryRunEvidence}
        cancelEvidence={factoryRunCancelEvidence}
        realtimePushActive={factoryRunRealtimePushActive}
        cancelDisabled={cancelFactoryRunDisabled}
        onRefresh={handleRefreshFactoryRun}
        onCancel={() => { void handleCancelFactoryRun(); }}
      />

      {/* 对话面板 */}
      <div className="flex-1 min-h-0">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}
