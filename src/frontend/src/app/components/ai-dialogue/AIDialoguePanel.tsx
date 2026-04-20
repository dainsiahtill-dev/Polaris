/**
 * AI 对话面板容器组件
 *
 * 主容器，协调各个子组件
 */

import { AlertCircle } from 'lucide-react';
import { AIDialogueHeader } from './AIDialogueHeader';
import { AIMessageList } from './AIMessageList';
import { AIInputArea } from './AIInputArea';
import { AIStatusBar, AIHistoryPanel } from './AIStatusBar';
import { useAIDialogue } from './useAIDialogue';
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
  /** 会话状态变化回调 */
  onSessionChange?: (sessionId: string | null) => void;
}

const DEFAULT_THEMES: Record<DialogueRole, NonNullable<AIDialoguePanelProps['roleTheme']>> = {
  pm: { primary: 'amber', secondary: 'amber-400', gradient: 'from-amber-500 to-amber-700' },
  architect: { primary: 'purple', secondary: 'purple-400', gradient: 'from-purple-500 to-purple-700' },
  director: { primary: 'emerald', secondary: 'emerald-400', gradient: 'from-emerald-500 to-emerald-700' },
  qa: { primary: 'rose', secondary: 'rose-400', gradient: 'from-rose-500 to-rose-700' },
};

/**
 * 获取状态显示组件
 */
function getStatusDisplay(
  statusKind: string,
  theme: NonNullable<AIDialoguePanelProps['roleTheme']>
): React.ReactNode {
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
  capabilityProfile,
  onSessionChange,
}: AIDialoguePanelProps) {
  const theme = roleTheme || DEFAULT_THEMES[dialogueRole];
  const defaultWelcome = `${roleDisplayName} 已就绪。您可以开始对话。`;
  const welcomeMessage = welcomeMessageProp || defaultWelcome;

  const {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    chatStatus,
    statusKind,
    isChatReady,
    isExplicitlyUnconfigured,
    conversationId,
    showHistory,
    conversations,
    configuredProviderLabel,
    configuredModelLabel,
    checkStatus,
    handleSend,
    handleClear,
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
    capabilityProfile,
    onSessionChange,
    onConversationChange,
  });

  if (!visible) return null;

  const statusDisplay = getStatusDisplay(statusKind, theme);

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
        isChatReady={isChatReady}
        statusKind={statusKind}
        onLoadHistory={handleToggleHistory}
        onClear={handleClear}
        onToggleHistory={handleToggleHistory}
      />

      <AIStatusBar
        statusKind={statusKind}
        roleName={roleDisplayName}
        error={chatStatus?.error}
        debug={chatStatus?.debug}
        theme={theme}
        onRetry={checkStatus}
      />

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
        isChatReady={isChatReady}
        isExplicitlyUnconfigured={isExplicitlyUnconfigured}
        roleName={roleDisplayName}
        theme={theme}
      />
    </div>
  );
}
