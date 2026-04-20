import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { AIDialoguePanel, AIDialoguePanelProps } from '@/app/components/ai-dialogue';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

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

/**
 * Director Workbench Panel - Director 角色工作台
 *
 * 基于 AIDialoguePanel，预配置为 Director（工部侍郎）角色。
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
  const [sessions, setSessions] = useState<Array<{ id: string; title: string; updated_at: string }>>([]);

  // 加载会话列表
  useEffect(() => {
    const loadSessions = async () => {
      try {
        const res = await apiFetch(
          `/v2/roles/sessions?role=director&host_kind=${hostKind}&limit=20`
        );
        const data = await res.json();
        if (data.ok && data.sessions) {
          setSessions(data.sessions);
        }
      } catch (err) {
        devLogger.error('[DirectorWorkbenchPanel] Failed to load sessions:', err);
      }
    };

    if (workspace) {
      loadSessions();
    }
  }, [workspace, hostKind]);

  const handleSessionChange = (newSessionId: string | null) => {
    setSessionId(newSessionId);
  };

  const handleNewSession = async () => {
    try {
      const res = await apiFetch('/v2/roles/sessions', {
        method: 'POST',
        body: JSON.stringify({
          role: 'director',
          host_kind: hostKind,
          workspace,
          attachment_mode: attachmentMode,
          context_config: {
            tasks_count: tasksCount,
            running_tasks: runningTasks,
          },
        }),
      });
      const data = await res.json();
      if (data.ok && data.session) {
        setSessionId(data.session.id);
        // 刷新会话列表
        const listRes = await apiFetch(
          `/v2/roles/sessions?role=director&host_kind=${hostKind}&limit=20`
        );
        const listData = await listRes.json();
        if (listData.ok && listData.sessions) {
          setSessions(listData.sessions);
        }
      }
    } catch (err) {
      devLogger.error('[DirectorWorkbenchPanel] Failed to create session:', err);
    }
  };

  const handleExportPatch = async () => {
    if (!sessionId) return;

    try {
      const res = await apiFetch(`/v2/roles/sessions/${sessionId}/actions/export-to-workflow`, {
        method: 'POST',
        body: JSON.stringify({
          target: 'director',
          export_kind: 'session_bundle',
          include_audit_log: true,
        }),
      });
      const data = await res.json();
      if (data.ok && data.run_id) {
        devLogger.debug('[DirectorWorkbenchPanel] Exported to workflow:', data);
        toast.success('已导出到 Director 工作流', {
          description: `Run ID: ${data.run_id}\nArtifacts: ${data.artifact_count || 0}`,
        });
      } else {
        devLogger.error('[DirectorWorkbenchPanel] Export failed:', data.error);
        toast.error('导出失败', {
          description: data.error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[DirectorWorkbenchPanel] Failed to export patch:', err);
      toast.error('导出失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const dialoguePanelProps: AIDialoguePanelProps = {
    dialogueRole: 'director',
    roleDisplayName: '工部侍郎',
    roleTheme: {
      primary: 'emerald',
      secondary: 'emerald-400',
      gradient: 'from-emerald-500 to-emerald-700',
    },
    welcomeMessage: '工部侍郎执行系统已就绪。您可以查看代码、编写文件、运行命令，或导出执行建议到正式流程。',
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

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-emerald-500/20 bg-emerald-500/5">
        <div className="flex items-center gap-2">
          <span className="text-sm text-emerald-600 font-medium">Director 工作台</span>
          {sessionId && (
            <span className="text-xs text-muted-foreground px-2 py-0.5 rounded bg-emerald-500/10">
              {sessionId.slice(0, 8)}...
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleNewSession}
            className="text-xs px-2 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-700 transition-colors"
          >
            新建会话
          </button>
          {sessionId && (
            <button
              onClick={handleExportPatch}
              className="text-xs px-2 py-1 rounded bg-blue-500/20 hover:bg-blue-500/30 text-blue-700 transition-colors"
            >
              导出补丁
            </button>
          )}
        </div>
      </div>

      {/* 对话面板 */}
      <div className="flex-1 min-h-0">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}
