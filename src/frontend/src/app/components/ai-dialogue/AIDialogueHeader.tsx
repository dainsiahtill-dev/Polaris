/**
 * AI 对话面板头部组件
 *
 * 显示角色图标、名称、状态和操作按钮
 */

import { useState, useCallback } from 'react';
import {
  Sparkles,
  History,
  RefreshCw,
  MoreHorizontal,
  AlertCircle,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import type { DialogueRole } from '@/services/conversationApi';

export interface AIDialogueHeaderProps {
  /** 角色主题 */
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  /** 角色显示名称 */
  roleName: string;
  /** 状态显示组件 */
  statusDisplay: React.ReactNode;
  /** 配置的 Provider 标签 */
  configuredProviderLabel: string;
  /** 配置的 Model 标签 */
  configuredModelLabel: string;
  /** 是否有进行中的对话 */
  hasConversation: boolean;
  /** 是否显示历史面板 */
  showHistory: boolean;
  /** 是否就绪 */
  isChatReady: boolean;
  /** 状态类型 */
  statusKind: string;
  /** 加载历史对话 */
  onLoadHistory: () => void;
  /** 清空对话 */
  onClear: () => void;
  /** 切换历史面板 */
  onToggleHistory: () => void;
}

/**
 * AI 对话面板头部
 */
export function AIDialogueHeader({
  theme,
  roleName,
  statusDisplay,
  configuredProviderLabel,
  configuredModelLabel,
  hasConversation,
  showHistory,
  isChatReady,
  statusKind,
  onLoadHistory,
  onClear,
  onToggleHistory,
}: AIDialogueHeaderProps) {
  return (
    <div className="h-14 flex items-center justify-between px-4 border-b border-white/10 bg-gradient-to-r from-slate-900 to-slate-950">
      {/* 左侧：角色信息 */}
      <div className="flex items-center gap-2">
        <div className={cn("w-7 h-7 rounded-lg bg-gradient-to-br flex items-center justify-center", theme.gradient)}>
          <Sparkles className="w-3.5 h-3.5 text-white" />
        </div>
        <div>
          <h3 className="text-sm font-medium text-slate-200">AI 助手</h3>
          <p className="text-[10px] text-slate-500">
            {isChatReady
              ? `${configuredProviderLabel} · ${configuredModelLabel}`
              : statusKind === 'unconfigured'
                ? `${roleName} 未配置`
                : `${roleName} 状态获取失败`}
          </p>
        </div>
      </div>

      {/* 右侧：操作按钮 */}
      <div className="flex items-center gap-1">
        {statusDisplay}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-slate-400 hover:text-slate-200 relative"
          onClick={() => {
            onToggleHistory();
            if (!showHistory) onLoadHistory();
          }}
          title="历史对话"
        >
          <History className="w-3.5 h-3.5" />
          {hasConversation && (
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-green-500 rounded-full" />
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-slate-400 hover:text-slate-200"
          onClick={onClear}
          title="清空对话"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 text-slate-400 hover:text-slate-200"
        >
          <MoreHorizontal className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}

/**
 * 获取状态警告消息
 */
export function getStatusWarningMessage(
  statusKind: string,
  roleName: string,
  error?: string
): { title: string; detail: string } {
  if (statusKind === 'unconfigured') {
    return {
      title: `${roleName} LLM 未配置`,
      detail: `请在设置中配置 ${roleName} 角色的 Provider 和 Model`,
    };
  }

  return {
    title: `${roleName} 状态获取失败`,
    detail: '状态检查请求失败，请先排查后端运行时、数据库初始化或网络错误。',
  };
}

/**
 * 主题颜色映射
 */
export const THEME_COLOR_MAP: Record<string, { bg: string; border: string; text: string }> = {
  amber: {
    bg: 'rgba(245, 158, 11, 0.1)',
    border: 'rgba(245, 158, 11, 0.2)',
    text: '#fbbf24',
  },
  purple: {
    bg: 'rgba(168, 85, 247, 0.1)',
    border: 'rgba(168, 85, 247, 0.2)',
    text: '#a78bfa',
  },
  emerald: {
    bg: 'rgba(16, 185, 129, 0.1)',
    border: 'rgba(16, 185, 129, 0.2)',
    text: '#34d399',
  },
  rose: {
    bg: 'rgba(244, 63, 94, 0.1)',
    border: 'rgba(244, 63, 94, 0.2)',
    text: '#fb7185',
  },
  cyan: {
    bg: 'rgba(6, 182, 212, 0.1)',
    border: 'rgba(6, 182, 212, 0.2)',
    text: '#22d3ee',
  },
  indigo: {
    bg: 'rgba(99, 102, 241, 0.1)',
    border: 'rgba(99, 102, 241, 0.2)',
    text: '#818cf8',
  },
  slate: {
    bg: 'rgba(148, 163, 184, 0.1)',
    border: 'rgba(148, 163, 184, 0.2)',
    text: '#94a3b8',
  },
};
