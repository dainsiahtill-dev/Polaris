import { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { AIDialoguePanel, AIDialoguePanelProps } from '@/app/components/ai-dialogue';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';

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

/**
 * PM Workbench Panel - PM 角色工作台
 *
 * 基于 AIDialoguePanel，预配置为 PM（尚书令）角色。
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
  const [sessions, setSessions] = useState<Array<{ id: string; title: string; updated_at: string }>>([]);
  const [showSessionSelector, setShowSessionSelector] = useState(false);

  // 加载会话列表
  useEffect(() => {
    const loadSessions = async () => {
      try {
        const res = await apiFetch(
          `/v2/roles/sessions?role=pm&host_kind=${hostKind}&limit=20`
        );
        const data = await res.json();
        if (data.ok && data.sessions) {
          setSessions(data.sessions);
        }
      } catch (err) {
        devLogger.error('[PMWorkbenchPanel] Failed to load sessions:', err);
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
          role: 'pm',
          host_kind: hostKind,
          workspace,
          attachment_mode: attachmentMode,
          context_config: {
            pm_running: pmRunning,
            task_count: taskCount,
          },
        }),
      });
      const data = await res.json();
      if (data.ok && data.session) {
        setSessionId(data.session.id);
        // 刷新会话列表
        const listRes = await apiFetch(
          `/v2/roles/sessions?role=pm&host_kind=${hostKind}&limit=20`
        );
        const listData = await listRes.json();
        if (listData.ok && listData.sessions) {
          setSessions(listData.sessions);
        }
      }
    } catch (err) {
      devLogger.error('[PMWorkbenchPanel] Failed to create session:', err);
    }
  };

  const handleExportToWorkflow = async () => {
    if (!sessionId) return;

    try {
      const res = await apiFetch(`/v2/roles/sessions/${sessionId}/actions/export-to-workflow`, {
        method: 'POST',
        body: JSON.stringify({
          target: 'pm',
          export_kind: 'session_bundle',
          include_audit_log: true,
        }),
      });
      const data = await res.json();
      if (data.ok && data.run_id) {
        devLogger.debug('[PMWorkbenchPanel] Exported to workflow:', data);
        toast.success('已导出到 PM 工作流', {
          description: `Run ID: ${data.run_id}\nArtifacts: ${data.artifact_count || 0}`,
        });
      } else {
        devLogger.error('[PMWorkbenchPanel] Export failed:', data.error);
        toast.error('导出失败', {
          description: data.error || '未知错误',
        });
      }
    } catch (err) {
      devLogger.error('[PMWorkbenchPanel] Failed to export:', err);
      toast.error('导出失败', {
        description: err instanceof Error ? err.message : '未知错误',
      });
    }
  };

  const dialoguePanelProps: AIDialoguePanelProps = {
    dialogueRole: 'pm',
    roleDisplayName: '尚书令',
    roleTheme: {
      primary: 'amber',
      secondary: 'amber-400',
      gradient: 'from-amber-500 to-amber-700',
    },
    welcomeMessage: '尚书令 PM 工作台已就绪。您可以创建任务计划、分析项目状态，或导出工作建议到正式流程。',
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

  return (
    <div className="flex flex-col h-full">
      {/* 工具栏 */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-amber-500/20 bg-amber-500/5">
        <div className="flex items-center gap-2">
          <span className="text-sm text-amber-600 font-medium">PM 工作台</span>
          {sessionId && (
            <span className="text-xs text-muted-foreground px-2 py-0.5 rounded bg-amber-500/10">
              {sessionId.slice(0, 8)}...
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
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

      {/* 对话面板 */}
      <div className="flex-1 min-h-0">
        <AIDialoguePanel {...dialoguePanelProps} />
      </div>
    </div>
  );
}
