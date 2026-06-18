import { useState, useRef, useEffect, useCallback } from 'react';
import { apiFetch } from '@/api';
import { useRuntimeTransport } from '@/runtime/transport';
import { getRoleChatStatus } from '@/services/llmService';
import type { ChatStatus } from '@/services/llmService';
import type { RoleChatRole } from '@/services/api.types';

export type DialogueRole = RoleChatRole;

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thinking?: string;
  timestamp: Date;
  isStreaming?: boolean;
  error?: boolean;
}

export interface UseRoleChatOptions {
  role: RoleChatRole;
  welcomeMessage?: string;
  context?: Record<string, unknown>;
}

export interface UseRoleChatReturn {
  messages: Message[];
  inputValue: string;
  setInputValue: (value: string) => void;
  isLoading: boolean;
  chatStatus: ChatStatus | null;
  statusLoading: boolean;
  sendMessage: () => Promise<void>;
  clearMessages: () => void;
  checkStatus: () => Promise<void>;
  handleKeyDown: (e: React.KeyboardEvent) => void;
}

interface ChatStreamEvent {
  type: 'thinking_chunk' | 'content_chunk' | 'complete' | 'error';
  data?: {
    content?: string;
    response?: string;
    message?: string;
    complete?: string;
    error?: string;
  };
}

interface JetstreamChatStartResponse {
  session_id?: string;
  status?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function appendWorkspaceQuery(path: string, workspace?: string): string {
  const value = String(workspace || '').trim();
  if (!value) return path;
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}workspace=${encodeURIComponent(value)}`;
}

function runtimeChatEvent(raw: unknown, channel: string): ChatStreamEvent | null {
  const message = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
  const event = message.type === 'EVENT' && message.event && typeof message.event === 'object'
    ? message.event as Record<string, unknown>
    : message;
  if (event.channel !== channel) return null;

  const payload = event.payload && typeof event.payload === 'object'
    ? event.payload as Record<string, unknown>
    : {};
  const type = typeof payload.type === 'string' ? payload.type : '';
  if (
    type !== 'thinking_chunk'
    && type !== 'content_chunk'
    && type !== 'complete'
    && type !== 'error'
  ) {
    return null;
  }
  const data = payload.data && typeof payload.data === 'object'
    ? payload.data as ChatStreamEvent['data']
    : {};
  return { type, data };
}

/**
 * 通用角色对话 Hook
 *
 * 支持任意角色（pm, architect, director, qa）的流式对话。
 * 可被多个组件复用。
 *
 * @example
 * ```tsx
 * const {
 *   messages,
 *   inputValue,
 *   setInputValue,
 *   isLoading,
 *   sendMessage,
 * } = useRoleChat({
 *   role: 'pm',
 *   welcomeMessage: 'PM ready',
 *   context: { workspace, taskCount },
 * });
 * ```
 */
export function useRoleChat(options: UseRoleChatOptions): UseRoleChatReturn {
  const { role, welcomeMessage = `${role} 已就绪`, context } = options;
  const workspace = typeof context?.workspace === 'string' ? context.workspace.trim() : '';

  const [messages, setMessages] = useState<Message[]>([
    {
      id: 'welcome',
      role: 'system',
      content: welcomeMessage,
      timestamp: new Date(),
    },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const abortControllerRef = useRef<AbortController | null>(null);

  // 检查角色LLM状态
  const checkStatus = useCallback(async () => {
    try {
      setStatusLoading(true);
      const result = await getRoleChatStatus(role, workspace);

      if (result.ok && result.data) {
        setChatStatus(result.data);
      } else {
        setChatStatus({
          ready: false,
          error: result.error || '无法获取状态',
        });
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '连接失败';
      setChatStatus({
        ready: false,
        error: errorMessage,
      });
    } finally {
      setStatusLoading(false);
    }
  }, [role, workspace]);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  // 清理函数
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  // 发送消息
  const sendMessage = useCallback(async () => {
    if (!inputValue.trim() || isLoading) return;

    if (!chatStatus?.ready) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'system',
          content: `${role} LLM 未就绪: ${chatStatus?.error || '请配置LLM设置'}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
      return;
    }

    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    // 创建新的 AbortController
    abortControllerRef.current = new AbortController();

    try {
      const response = await sendRoleChatMessage(
        role,
        { message: userMessage.content, context },
        abortControllerRef.current.signal,
        workspace
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      const messageId = (Date.now() + 1).toString();

      // 添加AI消息占位
      setMessages((prev) => [
        ...prev,
        {
          id: messageId,
          role: 'assistant',
          content: '',
          thinking: '',
          timestamp: new Date(),
          isStreaming: true,
        },
      ]);

      if (reader) {
        let buffer = '';
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            const event = parseSSEData(line);
            if (event) {
              handleStreamEvent(event, messageId, setMessages);
            } else if (line.startsWith('data: ')) {
              // Non-JSON data, append to content
              const data = line.slice(6);
              if (data !== '[DONE]') {
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === messageId
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
          m.id === messageId ? { ...m, isStreaming: false } : m
        )
      );
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // 用户取消，正常处理
        return;
      }
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
      abortControllerRef.current = null;
    }
  }, [inputValue, isLoading, chatStatus, role, context, workspace]);

  // 清空消息
  const clearMessages = useCallback(() => {
    setMessages([
      {
        id: 'welcome-new',
        role: 'system',
        content: `对话已清空。${welcomeMessage}`,
        timestamp: new Date(),
      },
    ]);
  }, [welcomeMessage]);

  // 键盘处理
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }, [sendMessage]);

  return {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    chatStatus,
    statusLoading,
    sendMessage,
    clearMessages,
    checkStatus,
    handleKeyDown,
  };
}

// 处理流式事件
import type { ChatStreamEvent } from '@/services';

function handleStreamEvent(
  event: ChatStreamEvent,
  messageId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
) {
  const eventType = event.type;
  const eventData = event.data;

  switch (eventType) {
    case 'thinking_chunk':
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, thinking: (m.thinking || '') + (eventData?.content || '') }
            : m
        )
      );
      break;

    case 'content_chunk':
      setMessages((prev) =>
        prev.map((m) =>
          m.id === messageId
            ? { ...m, content: m.content + (eventData?.content || '') }
            : m
        )
      );
      break;

    case 'complete':
      // 统一使用向后兼容的解析逻辑
      {
        const content = eventData?.content ?? eventData?.response ?? eventData?.complete ?? '';
        setMessages((prev) =>
          prev.map((m) =>
            m.id === messageId
              ? {
                  ...m,
                  content: content || m.content,
                  isStreaming: false,
                }
              : m
          )
        );
      }
      break;

    case 'error':
      // 统一使用向后兼容的解析逻辑
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
}
