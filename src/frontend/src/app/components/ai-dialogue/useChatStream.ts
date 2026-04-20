/**
 * Chat Stream Hook
 *
 * 处理流式对话的核心逻辑
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { apiFetch } from '@/api';

export interface ChatStreamMessage {
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

export interface ChatStreamOptions {
  /** 角色 */
  role: string;
  /** 会话ID */
  sessionId?: string | null;
  /** 工作区 */
  workspace?: string;
  /** 上下文 */
  context?: Record<string, unknown>;
  /** 对话历史 */
  history?: Array<{ role: string; content: string }>;
  /** 对话ID */
  conversationId?: string | null;
  /** 欢迎消息 */
  welcomeMessage?: string;
  /** 角色名称 */
  roleName?: string;
  /** 是否就绪 */
  isReady?: boolean;
  /** 状态检查错误 */
  statusError?: string;
  /** 是否未配置 */
  isExplicitlyUnconfigured?: boolean;
}

export interface ChatStreamReturn {
  /** 消息列表 */
  messages: ChatStreamMessage[];
  /** 输入值 */
  inputValue: string;
  /** 设置输入值 */
  setInputValue: (value: string) => void;
  /** 是否加载中 */
  isLoading: boolean;
  /** 对话ID */
  conversationId: string | null;
  /** 设置对话ID */
  setConversationId: (id: string | null) => void;
  /** 发送消息 */
  handleSend: () => Promise<void>;
  /** 清空消息 */
  handleClear: () => void;
  /** 检查状态 */
  checkStatus: () => Promise<void>;
  /** 键盘事件处理 */
  handleKeyDown: (e: React.KeyboardEvent) => void;
}

/**
 * 流式消息事件类型
 */
interface StreamEvent {
  type: string;
  data?: {
    content?: string;
    response?: string;
    message?: string;
    tool_name?: string;
    progress?: number;
    phase?: string;
    complete?: string;
    error?: string;
  };
}

/**
 * 使用聊天流
 */
export function useChatStream(options: ChatStreamOptions): ChatStreamReturn {
  const {
    role,
    sessionId,
    workspace,
    context,
    history = [],
    conversationId: initialConversationId,
    welcomeMessage = `${role} 已就绪。您可以开始对话。`,
    roleName = role,
    isReady = false,
    statusError,
    isExplicitlyUnconfigured = false,
  } = options;

  const [messages, setMessages] = useState<ChatStreamMessage[]>([
    {
      id: 'welcome',
      role: 'system',
      content: welcomeMessage,
      timestamp: new Date(),
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId ?? null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // 保存消息时的防抖定时器
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // 处理流式事件
  const handleStreamEvent = useCallback((
    event: StreamEvent,
    messageId: string
  ) => {
    const eventType = event.type;
    const eventData = event.data;

    switch (eventType) {
      case 'thinking_chunk':
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, thinking: (m.thinking || '') + (eventData?.content || ''), statusPhase: 'thinking' as const }
              : m
          )
        );
        break;

      case 'content_chunk':
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, content: m.content + (eventData?.content || ''), statusPhase: 'executing' as const }
              : m
          )
        );
        break;

      case 'tool_start':
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, toolName: eventData?.tool_name, statusPhase: 'tool_running' as const }
              : m
          )
        );
        break;

      case 'tool_progress':
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? { ...m, progress: eventData?.progress, statusPhase: 'tool_running' as const }
              : m
          )
        );
        break;

      case 'complete':
        {
          const content = eventData?.content ?? eventData?.response ?? eventData?.complete ?? '';
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId
                ? {
                    ...m,
                    content: content || m.content,
                    isStreaming: false,
                    statusPhase: 'completed' as const,
                  }
                : m
            )
          );
        }
        break;

      case 'error':
        {
          const errorMsg = eventData?.error ?? eventData?.message ?? '未知错误';
          setMessages((prev) =>
            prev.map((m) =>
              m.id === messageId
                ? {
                    ...m,
                    content: `错误: ${errorMsg}`,
                    isStreaming: false,
                    error: true,
                  }
                : m
            )
          );
        }
        break;
    }
  }, []);

  // 检查状态
  const checkStatus = useCallback(async () => {
    // 这个方法由父组件调用
  }, []);

  // 发送消息
  const handleSend = useCallback(async () => {
    if (!inputValue.trim() || isLoading) return;

    if (!isReady) {
      const unavailableReason = isExplicitlyUnconfigured
        ? statusError || `请配置${roleName}角色的LLM设置`
        : statusError || '当前无法获取角色状态，请稍后重试';

      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'system',
          content: isExplicitlyUnconfigured
            ? `${roleName} LLM 未就绪: ${unavailableReason}`
            : `${roleName} 暂时不可用: ${unavailableReason}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
      return;
    }

    const userMessage: ChatStreamMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const requestBody: Record<string, unknown> = {
        message: userMessage.content,
        context: {
          ...context,
          history,
          conversation_id: conversationId,
        },
      };

      if (sessionId) {
        requestBody.session_id = sessionId;
      }

      const res = await apiFetch(`/v2/role/${role}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let aiMessage: ChatStreamMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '',
        thinking: '',
        timestamp: new Date(),
        isStreaming: true,
        statusPhase: 'thinking',
      };

      setMessages((prev) => [...prev, aiMessage]);

      if (reader) {
        let buffer = '';
        let currentEventType = 'message';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('event: ')) {
              currentEventType = line.slice(7).trim();
            } else if (line.startsWith('data: ')) {
              const data = line.slice(6);
              if (data === '[DONE]') continue;

              try {
                const eventData = JSON.parse(data);
                handleStreamEvent({ type: currentEventType, data: eventData }, aiMessage.id);
              } catch {
                // 非JSON数据，可能是纯文本
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === aiMessage.id
                      ? { ...m, content: m.content + data }
                      : m
                  )
                );
              }
            }
          }
        }
      }

      // 标记流结束
      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMessage.id ? { ...m, isStreaming: false } : m
        )
      );
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '请求失败';
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: 'system',
          content: `错误: ${errorMessage}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [
    inputValue,
    isLoading,
    isReady,
    isExplicitlyUnconfigured,
    statusError,
    roleName,
    role,
    sessionId,
    workspace,
    context,
    history,
    conversationId,
    handleStreamEvent,
  ]);

  // 清空消息
  const handleClear = useCallback(() => {
    setMessages([
      {
        id: 'welcome-new',
        role: 'system',
        content: `对话已清空。${welcomeMessage}`,
        timestamp: new Date(),
      },
    ]);
  }, [welcomeMessage]);

  // 键盘事件处理
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  // 清理定时器
  useEffect(() => {
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, []);

  return {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    conversationId,
    setConversationId,
    handleSend,
    handleClear,
    checkStatus,
    handleKeyDown,
  };
}
