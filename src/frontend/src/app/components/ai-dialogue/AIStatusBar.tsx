/**
 * AI 状态栏组件
 *
 * 显示状态警告、错误信息和调试面板
 */

import { useState } from 'react';
import { AlertCircle, ChevronDown } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { THEME_COLOR_MAP, getStatusWarningMessage } from './AIDialogueHeader';

export interface AIStatusBarProps {
  /** 状态类型 */
  statusKind: string;
  /** 角色名称 */
  roleName: string;
  /** 错误信息 */
  error?: string;
  /** 调试信息 */
  debug?: Record<string, unknown>;
  /** 主题 */
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  /** 重新检查状态 */
  onRetry: () => void;
}

/**
 * 状态警告面板
 */
function StatusWarningPanel({
  statusKind,
  roleName,
  error,
  debug,
  theme,
  onRetry,
}: AIStatusBarProps) {
  const [showDebug, setShowDebug] = useState(false);

  if (statusKind === 'loading' || statusKind === 'ready') {
    return null;
  }

  const { title, detail } = getStatusWarningMessage(statusKind, roleName, error);

  return (
    <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/20">
      <div className="flex items-start gap-2">
        <AlertCircle className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-xs text-red-400 font-medium">{title}</p>
          <p className="text-[10px] text-red-400/70 mt-0.5">{detail}</p>
          {error && (
            <p className="text-[10px] text-red-400/50 mt-1 font-mono">
              错误: {error}
            </p>
          )}
          {/* Debug Info */}
          {debug && Object.keys(debug).length > 0 && (
            <div className="mt-2">
              <button
                onClick={() => setShowDebug(!showDebug)}
                className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-400"
              >
                {showDebug ? '隐藏调试信息' : '显示调试信息'}
                <ChevronDown className={cn('w-3 h-3 transition-transform', showDebug && 'rotate-180')} />
              </button>
              {showDebug && (
                <pre className="mt-1 p-2 rounded bg-slate-950 border border-white/5 text-[10px] text-slate-500 font-mono overflow-auto max-h-40">
                  {JSON.stringify(debug, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRetry}
          className="h-6 text-[10px] text-red-400 hover:text-red-300 hover:bg-red-500/10 flex-shrink-0"
        >
          重试
        </Button>
      </div>
    </div>
  );
}

/**
 * 历史对话列表面板
 */
export interface ConversationItem {
  id: string;
  title?: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface AIHistoryPanelProps {
  /** 对话列表 */
  conversations: ConversationItem[];
  /** 当前对话ID */
  currentConversationId: string | null;
  /** 主题 */
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  /** 欢迎消息 */
  welcomeMessage: string;
  /** 创建新对话 */
  onNewConversation: () => void;
  /** 切换对话 */
  onSelectConversation: (id: string) => void;
}

/**
 * 历史对话面板
 */
export function AIHistoryPanel({
  conversations,
  currentConversationId,
  theme,
  welcomeMessage,
  onNewConversation,
  onSelectConversation,
}: AIHistoryPanelProps) {
  const themeColors = THEME_COLOR_MAP[theme.primary] || THEME_COLOR_MAP.slate;

  return (
    <div className="border-b border-white/10 bg-slate-900/80">
      <div className="p-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-slate-400">历史对话</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 text-[10px] text-slate-400 hover:text-slate-200"
            onClick={onNewConversation}
          >
            + 新对话
          </Button>
        </div>
        <div className="max-h-48 overflow-auto space-y-1">
          {conversations.length === 0 ? (
            <p className="text-[10px] text-slate-500 text-center py-2">暂无历史对话</p>
          ) : (
            conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => onSelectConversation(conv.id)}
                className={cn(
                  'w-full text-left px-3 py-2 rounded-lg text-[11px] transition-colors',
                  conv.id === currentConversationId
                    ? 'text-slate-100'
                    : 'hover:bg-white/5 text-slate-300'
                )}
                style={conv.id === currentConversationId ? {
                  backgroundColor: themeColors.bg,
                  color: themeColors.text,
                } : undefined}
              >
                <div className="flex items-center justify-between">
                  <span className="truncate flex-1">{conv.title || '未命名对话'}</span>
                  <span className="text-[9px] text-slate-500 ml-2">
                    {new Date(conv.updated_at).toLocaleDateString('zh-CN')}
                  </span>
                </div>
                <div className="text-[9px] text-slate-500 mt-0.5">
                  {conv.message_count} 条消息
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * AI 状态栏
 */
export function AIStatusBar(props: AIStatusBarProps) {
  return <StatusWarningPanel {...props} />;
}
