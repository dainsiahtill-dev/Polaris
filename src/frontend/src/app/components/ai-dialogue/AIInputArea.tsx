/**
 * AI 输入区域组件
 *
 * 消息输入框和发送按钮
 */

import { memo, useCallback } from 'react';
import { Send } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { cn } from '@/app/components/ui/utils';
import { THEME_COLOR_MAP } from './AIDialogueHeader';

export interface AIInputAreaProps {
  /** 输入值 */
  value: string;
  /** 值变化回调 */
  onChange: (value: string) => void;
  /** 键盘事件 */
  onKeyDown: (e: React.KeyboardEvent) => void;
  /** 发送消息 */
  onSend: () => void;
  /** 是否正在加载 */
  isLoading: boolean;
  /** 是否就绪 */
  isChatReady: boolean;
  /** 是否未配置 */
  isExplicitlyUnconfigured: boolean;
  /** 外部阻塞原因 */
  blockedReason?: string;
  /** 角色名称 */
  roleName: string;
  /** 主题 */
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
}

/**
 * AI 输入区域 (memoized)
 */
const AIInputAreaComponent = memo(function AIInputArea({
  value,
  onChange,
  onKeyDown,
  onSend,
  isLoading,
  isChatReady,
  isExplicitlyUnconfigured,
  blockedReason,
  roleName,
  theme,
}: AIInputAreaProps) {
  // 获取占位符
  const getPlaceholder = (): string => {
    if (blockedReason) return blockedReason;
    if (isChatReady) return '输入消息...';
    if (isExplicitlyUnconfigured) return `请先配置 ${roleName} LLM...`;
    return `${roleName} 状态异常，请先重试`;
  };

  // 获取主题颜色
  const themeColors = THEME_COLOR_MAP[theme.primary] || THEME_COLOR_MAP.slate;

  return (
    <div className="p-4 border-t border-white/10 bg-slate-900/50">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={getPlaceholder()}
            disabled={isLoading || !isChatReady}
            className="pr-10 h-10 bg-slate-950 border-white/10 text-slate-200 placeholder:text-slate-600 disabled:opacity-50"
            style={{
              borderColor: themeColors.border,
            } as React.CSSProperties}
          />
          <Button
            size="icon"
            variant="ghost"
            className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 disabled:opacity-50 text-slate-400 hover:text-slate-200"
            onClick={onSend}
            disabled={!value.trim() || isLoading || !isChatReady}
          >
            <Send className="w-4 h-4" />
          </Button>
        </div>
      </div>
      <p className="text-[10px] text-slate-600 mt-2 text-center">
        {isChatReady
          ? '按 Enter 发送，Shift + Enter 换行'
          : blockedReason
            ? '解除阻塞后即可开始对话'
          : isExplicitlyUnconfigured
            ? '配置 LLM 后即可开始对话'
            : '恢复角色状态接口后即可开始对话'}
      </p>
    </div>
  );
});

export { AIInputAreaComponent as AIInputArea };
export default AIInputAreaComponent;
