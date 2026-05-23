/**
 * AI 对话面板容器组件
 *
 * 主容器，协调各个子组件
 */

import {
  Activity,
  AlertCircle,
  Clock,
  Database,
  Download,
  Eye,
  FileText,
  Link2,
  Link2Off,
  List as ListIcon,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import { AIDialogueHeader } from './AIDialogueHeader';
import { AIMessageList } from './AIMessageList';
import { AIInputArea } from './AIInputArea';
import { AIStatusBar, AIHistoryPanel } from './AIStatusBar';
import {
  useAIDialogue,
  type RoleSessionDetachStatus,
  type RoleSessionArtifactItem,
  type RoleSessionAuditEventItem,
  type RoleSessionDetailItem,
  type RoleSessionListItem,
  type RoleSessionMemoryDetailItem,
  type RoleSessionMemoryItem,
  type RoleSessionSnapshotExportFormat,
  type RoleSessionSnapshotExportStatus,
  type WorkflowExportStatus,
  type WorkflowExportTarget,
} from './useAIDialogue';
import { Button } from '@/app/components/ui/button';
import type { DialogueRole } from '@/services/conversationApi';
import type { ConversationItem } from './AIStatusBar';

export type { DialogueRole } from '@/services/conversationApi';

export interface AIDialoguePanelProps {
  /** 角色标识符 */
  dialogueRole: DialogueRole;
  /** 角色显示名称 */
  roleDisplayName: string;
  /** 角色图标/颜色主题 */
  roleTheme?: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  /** 欢迎消息 */
  welcomeMessage?: string;
  /** 上下文信息 */
  context?: Record<string, unknown>;
  /** 是否显示面板 */
  visible?: boolean;
  /** 初始对话ID */
  initialConversationId?: string;
  /** 工作区路径 */
  workspace?: string;
  /** 对话保存回调 */
  onConversationChange?: (conversationId: string | null) => void;
  /** Session ID */
  sessionId?: string;
  /** 宿主类型 */
  hostKind?: 'workflow' | 'electron_workbench' | 'tui' | 'cli' | 'api_server' | 'headless';
  /** 附着模式 */
  attachmentMode?: 'isolated' | 'attached_readonly' | 'attached_collaborative';
  /** 附着的工作流 Run ID */
  attachedRunId?: string;
  /** 附着的任务 ID */
  attachedTaskId?: string;
  /** 能力配置 */
  capabilityProfile?: Record<string, unknown> | string[];
  /** 显式导出到工作流的目标 */
  workflowExportTarget?: WorkflowExportTarget;
  /** 导出按钮标签 */
  workflowExportLabel?: string;
  /** 会话状态变化回调 */
  onSessionChange?: (sessionId: string | null) => void;
  /** 外部运行门禁阻塞原因 */
  interactionBlockedReason?: string;
}

const DEFAULT_THEMES: Record<DialogueRole, NonNullable<AIDialoguePanelProps['roleTheme']>> = {
  pm: { primary: 'amber', secondary: 'amber-400', gradient: 'from-amber-500 to-amber-700' },
  architect: { primary: 'purple', secondary: 'purple-400', gradient: 'from-purple-500 to-purple-700' },
  chief_engineer: { primary: 'cyan', secondary: 'cyan-400', gradient: 'from-cyan-500 to-cyan-700' },
  director: { primary: 'emerald', secondary: 'emerald-400', gradient: 'from-emerald-500 to-emerald-700' },
  qa: { primary: 'rose', secondary: 'rose-400', gradient: 'from-rose-500 to-rose-700' },
  scout: { primary: 'indigo', secondary: 'indigo-400', gradient: 'from-indigo-500 to-indigo-700' },
};

/**
 * 获取状态显示组件
 */
function getStatusDisplay(
  statusKind: string,
  theme: NonNullable<AIDialoguePanelProps['roleTheme']>
): React.ReactNode {
  if (statusKind === 'blocked') {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-amber-500/10 border border-amber-500/20">
        <AlertCircle className="w-3 h-3 text-amber-400" />
        <span className="text-[10px] text-amber-400">阻塞</span>
      </div>
    );
  }

  if (statusKind === 'loading') {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-slate-500/10 border border-slate-500/20">
        <div className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse" />
        <span className="text-[10px] text-slate-400">检查中...</span>
      </div>
    );
  }

  if (statusKind === 'unconfigured' || statusKind === 'error') {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-red-500/10 border border-red-500/20">
        <AlertCircle className="w-3 h-3 text-red-400" />
        <span className="text-[10px] text-red-400">
          {statusKind === 'unconfigured' ? '未配置' : '异常'}
        </span>
      </div>
    );
  }

  const colorMap: Record<string, { bg: string; border: string; dot: string; text: string }> = {
    amber: { bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.2)', dot: '#fbbf24', text: '#fbbf24' },
    purple: { bg: 'rgba(168, 85, 247, 0.1)', border: 'rgba(168, 85, 247, 0.2)', dot: '#a78bfa', text: '#a78bfa' },
    emerald: { bg: 'rgba(16, 185, 129, 0.1)', border: 'rgba(16, 185, 129, 0.2)', dot: '#34d399', text: '#34d399' },
    rose: { bg: 'rgba(244, 63, 94, 0.1)', border: 'rgba(244, 63, 94, 0.2)', dot: '#fb7185', text: '#fb7185' },
    cyan: { bg: 'rgba(6, 182, 212, 0.1)', border: 'rgba(6, 182, 212, 0.2)', dot: '#22d3ee', text: '#22d3ee' },
    indigo: { bg: 'rgba(99, 102, 241, 0.1)', border: 'rgba(99, 102, 241, 0.2)', dot: '#818cf8', text: '#818cf8' },
  };

  const colors = colorMap[theme.primary] || { bg: 'rgba(148, 163, 184, 0.1)', border: 'rgba(148, 163, 184, 0.2)', dot: '#94a3b8', text: '#94a3b8' };

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-full border" style={{ backgroundColor: colors.bg, borderColor: colors.border }}>
      <div className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: colors.dot }} />
      <span className="text-[10px]" style={{ color: colors.text }}>就绪</span>
    </div>
  );
}

function formatShortId(value?: string | null): string {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.length > 12 ? `${text.slice(0, 10)}...` : text;
}

function getAttachmentModeLabel(mode: string): string {
  if (mode === 'attached_readonly') return '只读附着';
  if (mode === 'attached_collaborative') return '协作附着';
  return '隔离';
}

function formatSessionTime(value?: string): string {
  const epoch = Date.parse(String(value || ''));
  if (!Number.isFinite(epoch)) return '';
  return new Date(epoch).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface RoleSessionStripProps {
  sessionId: string | null;
  isInitializingSession: boolean;
  sessionError: string;
  attachmentMode: string;
  attachedRunId?: string;
  attachedTaskId?: string;
  theme: NonNullable<AIDialoguePanelProps['roleTheme']>;
  workflowExportTarget?: WorkflowExportTarget;
  workflowExportLabel?: string;
  isExportingWorkflow: boolean;
  workflowExportStatus: WorkflowExportStatus;
  showRoleSessions: boolean;
  isLoadingRoleSessions: boolean;
  showRoleSessionEvidence: boolean;
  isLoadingRoleSessionEvidence: boolean;
  showRoleSessionMemory: boolean;
  isLoadingRoleSessionMemory: boolean;
  showRoleSessionSnapshotExport: boolean;
  isExportingRoleSessionSnapshot: boolean;
  roleSessionSnapshotExportStatus: RoleSessionSnapshotExportStatus;
  roleCapabilities: string[];
  isLoadingRoleCapabilities: boolean;
  roleCapabilitiesError: string;
  activeSessionDetail: RoleSessionDetailItem | null;
  isLoadingSessionDetail: boolean;
  sessionDetailError: string;
  isDetachingRoleSession: boolean;
  roleSessionDetachStatus: RoleSessionDetachStatus;
  onNewSession: () => void;
  onToggleRoleSessions: () => void;
  onToggleRoleSessionEvidence: () => void;
  onToggleRoleSessionMemory: () => void;
  onToggleRoleSessionSnapshotExport: () => void;
  onDetachRoleSession: () => void;
  onExportToWorkflow: () => void;
}

function RoleSessionStrip({
  sessionId,
  isInitializingSession,
  sessionError,
  attachmentMode,
  attachedRunId,
  attachedTaskId,
  theme,
  workflowExportTarget,
  workflowExportLabel,
  isExportingWorkflow,
  workflowExportStatus,
  showRoleSessions,
  isLoadingRoleSessions,
  showRoleSessionEvidence,
  isLoadingRoleSessionEvidence,
  showRoleSessionMemory,
  isLoadingRoleSessionMemory,
  showRoleSessionSnapshotExport,
  isExportingRoleSessionSnapshot,
  roleSessionSnapshotExportStatus,
  roleCapabilities,
  isLoadingRoleCapabilities,
  roleCapabilitiesError,
  activeSessionDetail,
  isLoadingSessionDetail,
  sessionDetailError,
  isDetachingRoleSession,
  roleSessionDetachStatus,
  onNewSession,
  onToggleRoleSessions,
  onToggleRoleSessionEvidence,
  onToggleRoleSessionMemory,
  onToggleRoleSessionSnapshotExport,
  onDetachRoleSession,
  onExportToWorkflow,
}: RoleSessionStripProps) {
  const themeColors: Record<string, string> = {
    amber: 'text-amber-300 border-amber-500/20 bg-amber-500/10',
    purple: 'text-purple-300 border-purple-500/20 bg-purple-500/10',
    cyan: 'text-cyan-300 border-cyan-500/20 bg-cyan-500/10',
    emerald: 'text-emerald-300 border-emerald-500/20 bg-emerald-500/10',
    rose: 'text-rose-300 border-rose-500/20 bg-rose-500/10',
    indigo: 'text-indigo-300 border-indigo-500/20 bg-indigo-500/10',
  };
  const tone = themeColors[theme.primary] || 'text-slate-300 border-slate-500/20 bg-slate-500/10';
  const hasDetailTaskId = Boolean(activeSessionDetail && Object.prototype.hasOwnProperty.call(activeSessionDetail, 'attached_task_id'));
  const hasDetailRunId = Boolean(activeSessionDetail && Object.prototype.hasOwnProperty.call(activeSessionDetail, 'attached_run_id'));
  const effectiveAttachmentMode = activeSessionDetail?.attachment_mode || attachmentMode;
  const effectiveAttachedTaskId = hasDetailTaskId ? activeSessionDetail?.attached_task_id || undefined : attachedTaskId;
  const effectiveAttachedRunId = hasDetailRunId ? activeSessionDetail?.attached_run_id || undefined : attachedRunId;
  const attachedTarget = effectiveAttachedTaskId
    ? `Task ${formatShortId(effectiveAttachedTaskId)}`
    : effectiveAttachedRunId
      ? `Run ${formatShortId(effectiveAttachedRunId)}`
      : '';
  const attachedTargetTitle = effectiveAttachedTaskId || effectiveAttachedRunId;
  const exportLabel = workflowExportLabel || '导出流程';
  const canExport = Boolean(workflowExportTarget);
  const canDetach = Boolean(sessionId && !isInitializingSession && (
    effectiveAttachmentMode !== 'isolated' || effectiveAttachedRunId || effectiveAttachedTaskId
  ));
  const exportStatusTone = workflowExportStatus.kind === 'success'
    ? 'border-emerald-400/20 bg-emerald-500/10 text-emerald-200'
    : 'border-red-400/20 bg-red-500/10 text-red-200';
  const detachStatusTone = roleSessionDetachStatus.kind === 'success'
    ? 'border-cyan-400/20 bg-cyan-500/10 text-cyan-100'
    : 'border-red-400/20 bg-red-500/10 text-red-200';
  const snapshotStatusTone = roleSessionSnapshotExportStatus.kind === 'success'
    ? 'border-indigo-400/20 bg-indigo-500/10 text-indigo-100'
    : 'border-red-400/20 bg-red-500/10 text-red-200';
  const detailTitle = activeSessionDetail
    ? [
      activeSessionDetail.title,
      activeSessionDetail.host_kind,
      activeSessionDetail.attachment_mode,
      activeSessionDetail.updated_at,
    ].filter(Boolean).join(' · ')
    : sessionDetailError || 'RoleSession 详情尚未加载';
  const detailLabel = isLoadingSessionDetail
    ? 'state ...'
    : activeSessionDetail
      ? `${activeSessionDetail.state || 'unknown'} · ${activeSessionDetail.message_count ?? 0} msg`
      : sessionDetailError
        ? 'state ?'
        : '';

  return (
    <div
      data-testid="ai-role-session-strip"
      className="flex min-h-10 items-center justify-between gap-2 border-b border-white/10 bg-slate-950/55 px-3 py-2 text-[11px]"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {isInitializingSession ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-slate-400" />
        ) : sessionId ? (
          <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-emerald-300" />
        ) : (
          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-amber-300" />
        )}
        <span className="shrink-0 text-slate-500">RoleSession</span>
        <span
          data-testid="ai-role-session-id"
          className="truncate font-mono text-slate-300"
          title={sessionId || sessionError || 'RoleSession 尚未创建'}
        >
          {isInitializingSession ? 'creating...' : sessionId ? formatShortId(sessionId) : 'unavailable'}
        </span>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 ${tone}`}>
          {getAttachmentModeLabel(effectiveAttachmentMode)}
        </span>
        {detailLabel ? (
          <span
            data-testid="ai-role-session-detail-chip"
            className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 ${
              sessionDetailError
                ? 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                : 'border-white/10 bg-white/5 text-slate-300'
            }`}
            title={detailTitle}
          >
            {isLoadingSessionDetail ? <Loader2 className="h-3 w-3 animate-spin" /> : <Clock className="h-3 w-3" />}
            <span>{detailLabel}</span>
          </span>
        ) : null}
        {attachedTarget ? (
          <span
            data-testid="ai-role-session-attachment"
            className="inline-flex min-w-0 items-center gap-1 truncate rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-slate-300"
            title={attachedTargetTitle}
          >
            <Link2 className="h-3 w-3 shrink-0" />
            <span className="truncate">{attachedTarget}</span>
          </span>
        ) : null}
        {roleSessionDetachStatus.kind !== 'idle' && roleSessionDetachStatus.message ? (
          <span
            data-testid="ai-role-session-detach-status"
            className={`inline-flex max-w-44 shrink items-center truncate rounded border px-1.5 py-0.5 ${detachStatusTone}`}
            title={roleSessionDetachStatus.message}
          >
            {roleSessionDetachStatus.message}
          </span>
        ) : null}
        {roleSessionSnapshotExportStatus.kind !== 'idle' && roleSessionSnapshotExportStatus.message ? (
          <span
            data-testid="ai-role-session-snapshot-status"
            className={`inline-flex max-w-44 shrink items-center truncate rounded border px-1.5 py-0.5 ${snapshotStatusTone}`}
            title={roleSessionSnapshotExportStatus.message}
          >
            {roleSessionSnapshotExportStatus.format || 'snapshot'}
          </span>
        ) : null}
        {workflowExportStatus.kind !== 'idle' && workflowExportStatus.message ? (
          <span
            data-testid="ai-role-session-export-status"
            className={`inline-flex max-w-40 shrink items-center truncate rounded border px-1.5 py-0.5 ${exportStatusTone}`}
            title={workflowExportStatus.runId || workflowExportStatus.message}
          >
            {workflowExportStatus.runId
              ? `Run ${formatShortId(workflowExportStatus.runId)}`
              : workflowExportStatus.message}
          </span>
        ) : null}
        <span
          data-testid="ai-role-capability-chip"
          className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 ${
            roleCapabilitiesError
              ? 'border-amber-400/20 bg-amber-500/10 text-amber-200'
              : 'border-white/10 bg-white/5 text-slate-300'
          }`}
          title={roleCapabilitiesError || roleCapabilities.join(', ') || '未加载角色能力'}
        >
          {isLoadingRoleCapabilities ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <ShieldCheck className="h-3 w-3" />
          )}
          <span>cap {isLoadingRoleCapabilities ? '...' : roleCapabilitiesError ? '?' : roleCapabilities.length}</span>
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleRoleSessions}
          data-testid="ai-role-session-list"
          className={`h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100 ${
            showRoleSessions ? 'bg-white/5 text-slate-100' : ''
          }`}
          title="查看 RoleSession 列表"
        >
          {isLoadingRoleSessions ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <ListIcon className="mr-1 h-3 w-3" />
          )}
          会话
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleRoleSessionEvidence}
          disabled={!sessionId || isInitializingSession}
          data-testid="ai-role-session-evidence-toggle"
          className={`h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${
            showRoleSessionEvidence ? 'bg-white/5 text-slate-100' : ''
          }`}
          title="查看 RoleSession 产物与审计"
        >
          {isLoadingRoleSessionEvidence ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Activity className="mr-1 h-3 w-3" />
          )}
          证据
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleRoleSessionMemory}
          disabled={!sessionId || isInitializingSession}
          data-testid="ai-role-session-memory-toggle"
          className={`h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${
            showRoleSessionMemory ? 'bg-white/5 text-slate-100' : ''
          }`}
          title="查看 Context OS RoleSession 记忆"
        >
          {isLoadingRoleSessionMemory ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Database className="mr-1 h-3 w-3" />
          )}
          记忆
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleRoleSessionSnapshotExport}
          disabled={!sessionId || isInitializingSession}
          data-testid="ai-role-session-snapshot-toggle"
          className={`h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100 disabled:opacity-50 ${
            showRoleSessionSnapshotExport ? 'bg-white/5 text-slate-100' : ''
          }`}
          title="导出当前 RoleSession 快照"
        >
          {isExportingRoleSessionSnapshot ? (
            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
          ) : (
            <Download className="mr-1 h-3 w-3" />
          )}
          快照
        </Button>
        {canDetach || isDetachingRoleSession ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onDetachRoleSession}
            disabled={!sessionId || isInitializingSession || isDetachingRoleSession}
            data-testid="ai-role-session-detach"
            className="h-6 px-2 text-[10px] text-slate-400 hover:bg-cyan-500/10 hover:text-cyan-100 disabled:opacity-50"
            title="解除当前 RoleSession 与工作流任务的附着"
          >
            {isDetachingRoleSession ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Link2Off className="mr-1 h-3 w-3" />
            )}
            解除
          </Button>
        ) : null}
        {canExport ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onExportToWorkflow}
            disabled={!sessionId || isInitializingSession || isExportingWorkflow}
            data-testid="ai-role-session-export"
            className="h-6 px-2 text-[10px] text-slate-400 hover:bg-emerald-500/10 hover:text-emerald-100 disabled:opacity-50"
            title={`导出当前 RoleSession 到 ${workflowExportTarget} 工作流`}
          >
            {isExportingWorkflow ? (
              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
            ) : (
              <Upload className="mr-1 h-3 w-3" />
            )}
            {exportLabel}
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="sm"
          onClick={onNewSession}
          disabled={isInitializingSession}
          data-testid="ai-role-session-new"
          className="h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
          title="新建 RoleSession"
        >
          <Plus className="mr-1 h-3 w-3" />
          新会话
        </Button>
      </div>
    </div>
  );
}

function compactPayload(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value.trim();
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function evidenceTime(value?: string): string {
  return formatSessionTime(value);
}

interface RoleSessionEvidencePanelProps {
  artifacts: RoleSessionArtifactItem[];
  auditEvents: RoleSessionAuditEventItem[];
  isLoading: boolean;
  error: string;
  onReload: () => void;
}

function RoleSessionEvidencePanel({
  artifacts,
  auditEvents,
  isLoading,
  error,
  onReload,
}: RoleSessionEvidencePanelProps) {
  return (
    <div data-testid="ai-role-session-evidence-panel" className="border-b border-white/10 bg-slate-900/85 px-3 py-2">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] text-slate-400">
          <FileText className="h-3.5 w-3.5 text-slate-500" />
          <span>RoleSession 证据</span>
          <span data-testid="ai-role-session-evidence-counts" className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-slate-300">
            {artifacts.length} artifacts / {auditEvents.length} audit
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onReload}
          disabled={isLoading}
          className="h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
          title="刷新 RoleSession 证据"
        >
          <RefreshCw className={`mr-1 h-3 w-3 ${isLoading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

      {error ? (
        <div className="mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {error}
        </div>
      ) : null}

      <div className="grid max-h-56 gap-2 overflow-auto md:grid-cols-2">
        <section className="min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2">
          <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
            <span>Artifacts</span>
            <span>{artifacts.length}</span>
          </div>
          {isLoading && artifacts.length === 0 ? (
            <div className="flex items-center gap-2 py-3 text-[11px] text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              加载产物...
            </div>
          ) : artifacts.length === 0 ? (
            <p className="py-3 text-[11px] text-slate-500">暂无产物</p>
          ) : (
            <div className="space-y-1">
              {artifacts.slice(0, 6).map((artifact) => {
                const payload = compactPayload(artifact.content || artifact.metadata);
                return (
                  <div key={artifact.id} data-testid="ai-role-session-artifact-row" className="rounded border border-white/5 bg-white/[0.035] px-2 py-1.5">
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="truncate font-mono text-slate-300">{formatShortId(artifact.id)}</span>
                      <span className="shrink-0 rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200">
                        {artifact.type || 'artifact'}
                      </span>
                    </div>
                    {payload ? (
                      <div className="mt-1 truncate text-[10px] text-slate-500" title={payload}>
                        {payload}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className="min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2">
          <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
            <span>Audit</span>
            <span>{auditEvents.length}</span>
          </div>
          {isLoading && auditEvents.length === 0 ? (
            <div className="flex items-center gap-2 py-3 text-[11px] text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              加载审计...
            </div>
          ) : auditEvents.length === 0 ? (
            <p className="py-3 text-[11px] text-slate-500">暂无审计事件</p>
          ) : (
            <div className="space-y-1">
              {auditEvents.slice(0, 8).map((event, index) => {
                const label = event.event_type || event.type || 'event';
                const time = evidenceTime(event.timestamp || event.created_at);
                const payload = compactPayload(event.payload || event.metadata);
                return (
                  <div key={event.id || `${label}-${index}`} data-testid="ai-role-session-audit-row" className="rounded border border-white/5 bg-white/[0.035] px-2 py-1.5">
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="truncate text-slate-300">{label}</span>
                      {time ? <span className="shrink-0 text-[9px] text-slate-500">{time}</span> : null}
                    </div>
                    {payload ? (
                      <div className="mt-1 truncate text-[10px] text-slate-500" title={payload}>
                        {payload}
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function memoryItemLabel(item: RoleSessionMemoryItem): string {
  return String(item.text || item.content || item.entity || item.path || item.id || '').trim();
}

function compactMemoryDetail(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value.trim();
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

interface RoleSessionMemoryPanelProps {
  query: string;
  items: RoleSessionMemoryItem[];
  detail: RoleSessionMemoryDetailItem | null;
  isLoading: boolean;
  error: string;
  isLoadingDetail: boolean;
  detailError: string;
  onQueryChange: (value: string) => void;
  onSearch: (query?: string) => void;
  onReadItem: (item: RoleSessionMemoryItem) => void;
}

function RoleSessionMemoryPanel({
  query,
  items,
  detail,
  isLoading,
  error,
  isLoadingDetail,
  detailError,
  onQueryChange,
  onSearch,
  onReadItem,
}: RoleSessionMemoryPanelProps) {
  const detailPayload = compactMemoryDetail(detail?.payload);

  return (
    <div data-testid="ai-role-session-memory-panel" className="border-b border-white/10 bg-slate-900/85 px-3 py-2">
      <form
        className="mb-2 flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          onSearch(query);
        }}
      >
        <div className="flex min-w-0 flex-1 items-center gap-2 text-[11px] text-slate-400">
          <Database className="h-3.5 w-3.5 text-slate-500" />
          <span>RoleSession 记忆</span>
          <input
            data-testid="ai-role-session-memory-query"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            className="h-7 min-w-0 flex-1 rounded-md border border-white/10 bg-slate-950/70 px-2 text-[11px] text-slate-200 outline-none placeholder:text-slate-600 focus:border-cyan-400/40"
            placeholder="task, artifact, state"
          />
        </div>
        <Button
          type="submit"
          variant="ghost"
          size="sm"
          disabled={isLoading}
          data-testid="ai-role-session-memory-search"
          className="h-7 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
          title="搜索 RoleSession 记忆"
        >
          <Search className={`mr-1 h-3 w-3 ${isLoading ? 'animate-pulse' : ''}`} />
          搜索
        </Button>
      </form>

      {error ? (
        <div className="mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {error}
        </div>
      ) : null}

      <div className="grid max-h-60 gap-2 overflow-auto md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2">
          <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
            <span>Matches</span>
            <span>{items.length}</span>
          </div>
          {isLoading && items.length === 0 ? (
            <div className="flex items-center gap-2 py-3 text-[11px] text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              检索中...
            </div>
          ) : items.length === 0 ? (
            <p className="py-3 text-[11px] text-slate-500">暂无记忆</p>
          ) : (
            <div className="space-y-1">
              {items.slice(0, 8).map((item, index) => {
                const label = memoryItemLabel(item);
                const kind = String(item.kind || 'memory');
                const key = item.id || `${kind}-${index}`;
                return (
                  <button
                    key={key}
                    type="button"
                    onClick={() => onReadItem(item)}
                    data-testid="ai-role-session-memory-row"
                    className="w-full rounded border border-white/5 bg-white/[0.035] px-2 py-1.5 text-left hover:border-cyan-400/20 hover:bg-cyan-500/10"
                  >
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="min-w-0 truncate text-slate-300">{label || formatShortId(item.id)}</span>
                      <span className="shrink-0 rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] text-cyan-200">
                        {kind}
                      </span>
                    </div>
                    {item.entity || item.path ? (
                      <div className="mt-1 truncate text-[10px] text-slate-500">
                        {item.entity || item.path}
                      </div>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <section data-testid="ai-role-session-memory-detail" className="min-w-0 rounded-md border border-white/10 bg-slate-950/45 p-2">
          <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
            <span>Detail</span>
            {isLoadingDetail ? <Loader2 className="h-3 w-3 animate-spin" /> : <Eye className="h-3 w-3" />}
          </div>
          {detailError ? (
            <div className="rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
              {detailError}
            </div>
          ) : detailPayload ? (
            <pre className="max-h-44 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950/60 p-2 text-[10px] leading-4 text-slate-300">
              {detailPayload}
            </pre>
          ) : (
            <p className="py-3 text-[11px] text-slate-500">选择一条记忆</p>
          )}
        </section>
      </div>
    </div>
  );
}

function formatSnapshotPayload(payload: unknown, format: RoleSessionSnapshotExportFormat): string {
  if (payload === undefined || payload === null) return '';
  if (
    format === 'markdown'
    && typeof payload === 'object'
    && payload
    && typeof (payload as Record<string, unknown>).markdown === 'string'
  ) {
    return String((payload as Record<string, unknown>).markdown);
  }
  if (typeof payload === 'string') return payload;
  try {
    return JSON.stringify(payload, null, 2);
  } catch {
    return String(payload);
  }
}

interface RoleSessionSnapshotExportPanelProps {
  format: RoleSessionSnapshotExportFormat;
  payload: unknown;
  isLoading: boolean;
  status: RoleSessionSnapshotExportStatus;
  onFormatChange: (format: RoleSessionSnapshotExportFormat) => void;
  onExport: (format?: RoleSessionSnapshotExportFormat) => void;
}

function RoleSessionSnapshotExportPanel({
  format,
  payload,
  isLoading,
  status,
  onFormatChange,
  onExport,
}: RoleSessionSnapshotExportPanelProps) {
  const preview = formatSnapshotPayload(payload, format);
  const statusTone = status.kind === 'error'
    ? 'border-red-500/20 bg-red-500/10 text-red-200'
    : 'border-white/10 bg-white/5 text-slate-300';

  const handleFormatChange = (nextFormat: RoleSessionSnapshotExportFormat) => {
    onFormatChange(nextFormat);
    onExport(nextFormat);
  };

  return (
    <div data-testid="ai-role-session-snapshot-panel" className="border-b border-white/10 bg-slate-900/85 px-3 py-2">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-[11px] text-slate-400">
          <Download className="h-3.5 w-3.5 text-slate-500" />
          <span>RoleSession 快照</span>
          {status.message ? (
            <span
              data-testid="ai-role-session-snapshot-message"
              className={`truncate rounded border px-1.5 py-0.5 text-[10px] ${statusTone}`}
              title={status.message}
            >
              {status.message}
            </span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {(['json', 'markdown'] as const).map((option) => (
            <Button
              key={option}
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => handleFormatChange(option)}
              disabled={isLoading}
              data-testid={`ai-role-session-snapshot-format-${option}`}
              className={`h-6 px-2 text-[10px] ${
                format === option
                  ? 'bg-white/10 text-slate-100'
                  : 'text-slate-400 hover:bg-white/5 hover:text-slate-100'
              }`}
              title={`导出 ${option.toUpperCase()} 快照`}
            >
              {option === 'json' ? 'JSON' : 'MD'}
            </Button>
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onExport(format)}
            disabled={isLoading}
            data-testid="ai-role-session-snapshot-refresh"
            className="h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
            title="刷新 RoleSession 快照"
          >
            <RefreshCw className={`mr-1 h-3 w-3 ${isLoading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        </div>
      </div>

      {status.kind === 'error' ? (
        <div className="mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {status.message}
        </div>
      ) : null}

      <pre
        data-testid="ai-role-session-snapshot-preview"
        className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-white/10 bg-slate-950/55 p-2 text-[10px] leading-4 text-slate-300"
      >
        {isLoading && !preview ? '导出中...' : preview || '暂无快照'}
      </pre>
    </div>
  );
}

interface RoleSessionListPanelProps {
  sessions: RoleSessionListItem[];
  activeSessionId: string | null;
  isLoading: boolean;
  error: string;
  theme: NonNullable<AIDialoguePanelProps['roleTheme']>;
  onReload: () => void;
  onSelect: (id: string) => void;
}

function RoleSessionListPanel({
  sessions,
  activeSessionId,
  isLoading,
  error,
  theme,
  onReload,
  onSelect,
}: RoleSessionListPanelProps) {
  const themeColors: Record<string, string> = {
    amber: 'border-amber-500/25 bg-amber-500/10 text-amber-100',
    purple: 'border-purple-500/25 bg-purple-500/10 text-purple-100',
    cyan: 'border-cyan-500/25 bg-cyan-500/10 text-cyan-100',
    emerald: 'border-emerald-500/25 bg-emerald-500/10 text-emerald-100',
    rose: 'border-rose-500/25 bg-rose-500/10 text-rose-100',
    indigo: 'border-indigo-500/25 bg-indigo-500/10 text-indigo-100',
  };
  const activeTone = themeColors[theme.primary] || 'border-slate-500/25 bg-slate-500/10 text-slate-100';

  return (
    <div data-testid="ai-role-session-list-panel" className="border-b border-white/10 bg-slate-900/85 px-3 py-2">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] text-slate-400">RoleSession 历史</span>
        <Button
          variant="ghost"
          size="sm"
          onClick={onReload}
          disabled={isLoading}
          className="h-6 px-2 text-[10px] text-slate-400 hover:bg-white/5 hover:text-slate-100"
          title="刷新 RoleSession 列表"
        >
          <RefreshCw className={`mr-1 h-3 w-3 ${isLoading ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>
      {error ? (
        <div className="mb-2 rounded border border-red-500/20 bg-red-500/10 px-2 py-1 text-[10px] text-red-200">
          {error}
        </div>
      ) : null}
      <div className="max-h-48 space-y-1 overflow-auto">
        {isLoading && sessions.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-4 text-[11px] text-slate-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            加载 RoleSession...
          </div>
        ) : sessions.length === 0 ? (
          <p className="py-4 text-center text-[11px] text-slate-500">暂无可恢复的 RoleSession</p>
        ) : (
          sessions.map((session) => {
            const updatedAt = formatSessionTime(session.updated_at || session.created_at);
            const isActive = session.id === activeSessionId;
            return (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelect(session.id)}
                data-testid={`ai-role-session-option-${session.id}`}
                className={`w-full rounded-md border px-2 py-2 text-left text-[11px] transition-colors ${
                  isActive
                    ? activeTone
                    : 'border-white/10 bg-white/[0.035] text-slate-300 hover:border-white/20 hover:bg-white/[0.06]'
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-mono">{formatShortId(session.id)}</span>
                  {session.state ? (
                    <span className="shrink-0 rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[9px] uppercase text-slate-400">
                      {session.state}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1 flex min-w-0 items-center gap-2 text-[10px] text-slate-500">
                  {updatedAt ? (
                    <span className="inline-flex shrink-0 items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {updatedAt}
                    </span>
                  ) : null}
                  <span className="truncate">
                    {session.title || getAttachmentModeLabel(session.attachment_mode || 'isolated')}
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

/**
 * AI 对话面板
 */
export function AIDialoguePanel({
  dialogueRole,
  roleDisplayName,
  roleTheme,
  welcomeMessage: welcomeMessageProp,
  context,
  visible = true,
  initialConversationId,
  workspace,
  onConversationChange,
  sessionId,
  hostKind = 'electron_workbench',
  attachmentMode = 'isolated',
  attachedRunId,
  attachedTaskId,
  capabilityProfile,
  workflowExportTarget,
  workflowExportLabel,
  onSessionChange,
  interactionBlockedReason,
}: AIDialoguePanelProps) {
  const theme = roleTheme || DEFAULT_THEMES[dialogueRole];
  const defaultWelcome = `${roleDisplayName} 已就绪。您可以开始对话。`;
  const welcomeMessage = welcomeMessageProp || defaultWelcome;
  const blockedReason = String(interactionBlockedReason || '').trim();
  const isInteractionBlocked = Boolean(blockedReason);

  const {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    chatStatus,
    statusKind,
    isChatReady,
    isExplicitlyUnconfigured,
    sessionId: activeSessionId,
    isInitializingSession,
    sessionError,
    isExportingWorkflow,
    workflowExportStatus,
    showRoleSessions,
    roleSessions,
    isLoadingRoleSessions,
    roleSessionListError,
    showRoleSessionEvidence,
    roleSessionArtifacts,
    roleSessionAuditEvents,
    isLoadingRoleSessionEvidence,
    roleSessionEvidenceError,
    showRoleSessionMemory,
    roleSessionMemoryQuery,
    roleSessionMemoryItems,
    isLoadingRoleSessionMemory,
    roleSessionMemoryError,
    roleSessionMemoryDetail,
    isLoadingRoleSessionMemoryDetail,
    roleSessionMemoryDetailError,
    showRoleSessionSnapshotExport,
    roleSessionSnapshotExportFormat,
    roleSessionSnapshotExportPayload,
    isExportingRoleSessionSnapshot,
    roleSessionSnapshotExportStatus,
    roleCapabilities,
    isLoadingRoleCapabilities,
    roleCapabilitiesError,
    activeRoleSessionDetail,
    isLoadingRoleSessionDetail,
    roleSessionDetailError,
    isDetachingRoleSession,
    roleSessionDetachStatus,
    conversationId,
    showHistory,
    conversations,
    configuredProviderLabel,
    configuredModelLabel,
    checkStatus,
    handleSend,
    handleClear,
    handleNewRoleSession,
    handleLoadRoleSessions,
    handleToggleRoleSessions,
    handleSelectRoleSession,
    handleLoadRoleSessionEvidence,
    handleToggleRoleSessionEvidence,
    setRoleSessionMemoryQuery,
    handleLoadRoleSessionMemory,
    handleToggleRoleSessionMemory,
    handleReadRoleSessionMemoryItem,
    setRoleSessionSnapshotExportFormat,
    handleExportRoleSessionSnapshot,
    handleToggleRoleSessionSnapshotExport,
    handleDetachRoleSession,
    handleExportToWorkflow,
    handleKeyDown,
    handleToggleHistory,
    handleNewConversation,
    handleSelectConversation,
  } = useAIDialogue({
    role: dialogueRole,
    roleName: roleDisplayName,
    welcomeMessage,
    context,
    workspace,
    initialConversationId,
    sessionId,
    hostKind,
    attachmentMode,
    attachedRunId,
    attachedTaskId,
    capabilityProfile,
    workflowExportTarget,
    onSessionChange,
    onConversationChange,
  });

  if (!visible) return null;

  const effectiveStatusKind = isInteractionBlocked ? 'blocked' : statusKind;
  const effectiveIsChatReady = isChatReady && !isInteractionBlocked;
  const statusDisplay = getStatusDisplay(effectiveStatusKind, theme);

  return (
    <div className="h-full flex flex-col bg-slate-950/50 border-l border-white/10">
      <AIDialogueHeader
        theme={theme}
        roleName={roleDisplayName}
        statusDisplay={statusDisplay}
        configuredProviderLabel={configuredProviderLabel}
        configuredModelLabel={configuredModelLabel}
        hasConversation={!!conversationId}
        showHistory={showHistory}
        isChatReady={effectiveIsChatReady}
        statusKind={effectiveStatusKind}
        onLoadHistory={handleToggleHistory}
        onClear={handleClear}
        onToggleHistory={handleToggleHistory}
      />

      <AIStatusBar
        statusKind={effectiveStatusKind}
        roleName={roleDisplayName}
        error={blockedReason || chatStatus?.error}
        debug={chatStatus?.debug}
        theme={theme}
        onRetry={checkStatus}
      />

      <RoleSessionStrip
        sessionId={activeSessionId}
        isInitializingSession={isInitializingSession}
        sessionError={sessionError}
        attachmentMode={attachmentMode}
        attachedRunId={attachedRunId}
        attachedTaskId={attachedTaskId}
        theme={theme}
        workflowExportTarget={workflowExportTarget}
        workflowExportLabel={workflowExportLabel}
        isExportingWorkflow={isExportingWorkflow}
        workflowExportStatus={workflowExportStatus}
        showRoleSessions={showRoleSessions}
        isLoadingRoleSessions={isLoadingRoleSessions}
        showRoleSessionEvidence={showRoleSessionEvidence}
        isLoadingRoleSessionEvidence={isLoadingRoleSessionEvidence}
        showRoleSessionMemory={showRoleSessionMemory}
        isLoadingRoleSessionMemory={isLoadingRoleSessionMemory}
        showRoleSessionSnapshotExport={showRoleSessionSnapshotExport}
        isExportingRoleSessionSnapshot={isExportingRoleSessionSnapshot}
        roleSessionSnapshotExportStatus={roleSessionSnapshotExportStatus}
        roleCapabilities={roleCapabilities}
        isLoadingRoleCapabilities={isLoadingRoleCapabilities}
        roleCapabilitiesError={roleCapabilitiesError}
        activeSessionDetail={activeRoleSessionDetail}
        isLoadingSessionDetail={isLoadingRoleSessionDetail}
        sessionDetailError={roleSessionDetailError}
        isDetachingRoleSession={isDetachingRoleSession}
        roleSessionDetachStatus={roleSessionDetachStatus}
        onNewSession={handleNewRoleSession}
        onToggleRoleSessions={handleToggleRoleSessions}
        onToggleRoleSessionEvidence={handleToggleRoleSessionEvidence}
        onToggleRoleSessionMemory={handleToggleRoleSessionMemory}
        onToggleRoleSessionSnapshotExport={handleToggleRoleSessionSnapshotExport}
        onDetachRoleSession={handleDetachRoleSession}
        onExportToWorkflow={handleExportToWorkflow}
      />

      {showRoleSessionEvidence && (
        <RoleSessionEvidencePanel
          artifacts={roleSessionArtifacts}
          auditEvents={roleSessionAuditEvents}
          isLoading={isLoadingRoleSessionEvidence}
          error={roleSessionEvidenceError}
          onReload={handleLoadRoleSessionEvidence}
        />
      )}

      {showRoleSessionMemory && (
        <RoleSessionMemoryPanel
          query={roleSessionMemoryQuery}
          items={roleSessionMemoryItems}
          detail={roleSessionMemoryDetail}
          isLoading={isLoadingRoleSessionMemory}
          error={roleSessionMemoryError}
          isLoadingDetail={isLoadingRoleSessionMemoryDetail}
          detailError={roleSessionMemoryDetailError}
          onQueryChange={setRoleSessionMemoryQuery}
          onSearch={(query) => { void handleLoadRoleSessionMemory(query); }}
          onReadItem={(item) => { void handleReadRoleSessionMemoryItem(item); }}
        />
      )}

      {showRoleSessionSnapshotExport && (
        <RoleSessionSnapshotExportPanel
          format={roleSessionSnapshotExportFormat}
          payload={roleSessionSnapshotExportPayload}
          isLoading={isExportingRoleSessionSnapshot}
          status={roleSessionSnapshotExportStatus}
          onFormatChange={setRoleSessionSnapshotExportFormat}
          onExport={(format) => { void handleExportRoleSessionSnapshot(format); }}
        />
      )}

      {showRoleSessions && (
        <RoleSessionListPanel
          sessions={roleSessions}
          activeSessionId={activeSessionId}
          isLoading={isLoadingRoleSessions}
          error={roleSessionListError}
          theme={theme}
          onReload={handleLoadRoleSessions}
          onSelect={(id) => { void handleSelectRoleSession(id); }}
        />
      )}

      {showHistory && (
        <AIHistoryPanel
          conversations={conversations as unknown as ConversationItem[]}
          currentConversationId={conversationId}
          theme={theme}
          welcomeMessage={welcomeMessage}
          onNewConversation={handleNewConversation}
          onSelectConversation={handleSelectConversation}
        />
      )}

      <AIMessageList
        messages={messages}
        isLoading={isLoading}
        theme={theme}
        roleName={roleDisplayName}
      />

      <AIInputArea
        value={inputValue}
        onChange={setInputValue}
        onKeyDown={handleKeyDown}
        onSend={handleSend}
        isLoading={isLoading}
        isChatReady={effectiveIsChatReady}
        isExplicitlyUnconfigured={isExplicitlyUnconfigured}
        blockedReason={blockedReason}
        roleName={roleDisplayName}
        theme={theme}
      />
    </div>
  );
}
