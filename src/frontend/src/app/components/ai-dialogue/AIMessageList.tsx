/**
 * AI 消息列表组件
 *
 * 渲染对话消息气泡，支持流式状态显示
 */

import { useState, useRef, useEffect } from 'react';
import {
  User,
  Bot,
  AlertCircle,
  Copy,
  Check,
  Brain,
  ChevronDown,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { SmartContentRenderer } from './SmartContentRenderer';
import { ManusStyleStatusIndicator } from './ManusStyleStatusIndicator';

export interface AIMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  timestamp: Date;
  isStreaming?: boolean;
  error?: boolean;
  toolName?: string;
  progress?: number;
  statusPhase?: 'idle' | 'thinking' | 'executing' | 'tool_running' | 'completed' | 'error';
}

export interface AIMessageListProps {
  /** 消息列表 */
  messages: AIMessage[];
  /** 是否正在加载 */
  isLoading: boolean;
  /** 主题配置 */
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  /** 欢迎消息 */
  welcomeMessage?: string;
  /** 角色名称 */
  roleName?: string;
}

/**
 * 消息气泡组件
 */
interface MessageBubbleProps {
  message: AIMessage;
  theme: {
    primary: string;
    secondary: string;
    gradient: string;
  };
  onCopy: (content: string) => void;
}

function MessageBubble({ message, theme, onCopy }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';
  const isError = message.error;
  const [showThinking, setShowThinking] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Ignore copy errors
    }
  };

  // 获取消息气泡背景色
  const getBubbleBgClass = (): string => {
    if (isUser) {
      switch (theme.primary) {
        case 'amber': return 'bg-amber-600 text-white rounded-tr-sm';
        case 'purple': return 'bg-purple-600 text-white rounded-tr-sm';
        case 'emerald': return 'bg-emerald-600 text-white rounded-tr-sm';
        case 'rose': return 'bg-rose-600 text-white rounded-tr-sm';
        case 'cyan': return 'bg-cyan-600 text-white rounded-tr-sm';
        case 'indigo': return 'bg-indigo-600 text-white rounded-tr-sm';
        default: return 'bg-slate-600 text-white rounded-tr-sm';
      }
    }
    if (isError) {
      return 'bg-red-500/10 text-red-400 rounded-tl-sm border border-red-500/20';
    }
    if (isSystem) {
      return 'bg-slate-800/80 text-slate-400 rounded-tl-sm border border-white/5';
    }
    return 'bg-slate-800 text-slate-200 rounded-tl-sm border border-white/10';
  };

  // 获取头像样式
  const getAvatarClass = (): string => {
    if (isUser) return 'bg-slate-700';
    if (isError) return 'bg-red-500/20';
    if (isSystem) return 'bg-slate-800';
    return cn('bg-gradient-to-br', theme.gradient);
  };

  return (
    <div className={cn('group flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}>
      {/* Avatar */}
      <div className={cn('w-7 h-7 rounded-lg flex-shrink-0 flex items-center justify-center', getAvatarClass())}>
        {isUser ? (
          <User className="w-3.5 h-3.5 text-slate-300" />
        ) : isError ? (
          <AlertCircle className="w-3.5 h-3.5 text-red-400" />
        ) : (
          <Bot className="w-3.5 h-3.5 text-white" />
        )}
      </div>

      {/* Content */}
      <div className={cn('flex-1 max-w-[85%]', isUser ? 'text-right' : 'text-left')}>
        <div className={cn('inline-block text-left px-3 py-2 rounded-2xl text-sm relative', getBubbleBgClass())}>
          <SmartContentRenderer content={message.content} />

          {/* Copy button */}
          {!isUser && !isSystem && !isError && (
            <button
              onClick={handleCopy}
              className="absolute -right-8 top-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-md hover:bg-white/5 text-slate-400 hover:text-slate-200"
              title="复制"
            >
              {copied ? (
                <Check className="w-3.5 h-3.5 text-emerald-400" />
              ) : (
                <Copy className="w-3.5 h-3.5" />
              )}
            </button>
          )}
        </div>

        {/* Thinking section */}
        {!isUser && !isSystem && !isError && message.thinking && (
          <div className="mt-2">
            <button
              onClick={() => setShowThinking(!showThinking)}
              className="flex items-center gap-1 text-[10px] text-slate-500 hover:text-slate-400"
            >
              <Brain className="w-3 h-3" />
              {showThinking ? '隐藏思考过程' : '显示思考过程'}
              <ChevronDown className={cn('w-3 h-3 transition-transform', showThinking && 'rotate-180')} />
            </button>
            {showThinking && (
              <div className="mt-1 p-2 rounded-lg bg-slate-950/50 border border-white/5">
                <p className="text-[11px] text-slate-500 whitespace-pre-wrap">{message.thinking}</p>
              </div>
            )}
          </div>
        )}

        {/* Timestamp */}
        <p className="text-[10px] text-slate-600 mt-1">
          {message.timestamp.toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </p>
      </div>
    </div>
  );
}

/**
 * AI 消息列表
 */
export function AIMessageList({
  messages,
  isLoading,
  theme,
  roleName,
}: AIMessageListProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;

  return (
    <div className="flex-1 overflow-auto p-4 space-y-4">
      {messages.map((message, index) => (
        <MessageBubble
          key={message.id}
          message={message}
          theme={theme}
          onCopy={() => {}}
        />
      ))}
      {isLoading && lastMessage && (
        <ManusStyleStatusIndicator
          phase={lastMessage.statusPhase || 'thinking'}
          message={lastMessage.thinking ? '正在思考...' : lastMessage.content ? '生成回复中...' : '等待响应...'}
          thinking={lastMessage.thinking}
          toolName={lastMessage.toolName}
          progress={lastMessage.progress}
          theme={theme.primary as 'indigo' | 'amber' | 'cyan' | 'emerald'}
        />
      )}
      <div ref={messagesEndRef} />
    </div>
  );
}
