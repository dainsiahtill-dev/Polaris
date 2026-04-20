/**
 * AI 对话核心 Hook
 *
 * 处理对话状态、消息、流式事件等核心逻辑
 */

import { useState, useRef, useCallback, useEffect } from 'react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import {
  getConversation,
  listConversations,
  saveFullConversation,
  type Conversation,
  type MessageRole,
} from '@/services/conversationApi';
import { resolveDialogueStatusKind, type DialogueChatStatus } from './chatStatusState';
import type { AIMessage } from './AIMessageList';

interface ChatStatus extends DialogueChatStatus {
  error?: string;
  role?: string;
  llm_test_ready?: boolean;
  role_config?: {
    provider_id: string;
    model: string;
    profile?: string;
  };
  provider_type?: string;
  supports_streaming?: boolean;
  debug?: Record<string, unknown>;
}

export interface UseAIDialogueOptions {
  /** 角色 */
  role: string;
  /** 角色名称 */
  roleName: string;
  /** 欢迎消息 */
  welcomeMessage: string;
  /** 上下文 */
  context?: Record<string, unknown>;
  /** 工作区 */
  workspace?: string;
  /** 初始对话ID */
  initialConversationId?: string;
  /** 会话ID */
  sessionId?: string | null;
  /** 宿主类型 */
  hostKind?: string;
  /** 附着模式 */
  attachmentMode?: string;
  /** 能力配置 */
  capabilityProfile?: Record<string, unknown> | string[];
  /** 会话状态变化回调 */
  onSessionChange?: (sessionId: string | null) => void;
  /** 对话变化回调 */
  onConversationChange?: (conversationId: string | null) => void;
}

export interface UseAIDialogueReturn {
  // 状态
  messages: AIMessage[];
  inputValue: string;
  setInputValue: (value: string) => void;
  isLoading: boolean;
  chatStatus: ChatStatus | null;
  statusLoading: boolean;
  statusKind: string;
  isChatReady: boolean;
  isExplicitlyUnconfigured: boolean;
  conversationId: string | null;
  showHistory: boolean;
  conversations: Conversation[];
  /** 状态显示 */
  configuredProviderLabel: string;
  configuredModelLabel: string;
  /** 操作方法 */
  checkStatus: () => Promise<void>;
  handleSend: () => Promise<void>;
  handleClear: () => void;
  handleKeyDown: (e: React.KeyboardEvent) => void;
  handleToggleHistory: () => void;
  handleNewConversation: () => void;
  handleSelectConversation: (id: string) => void;
  handleLoadHistory: () => Promise<void>;
}

export function useAIDialogue(options: UseAIDialogueOptions): UseAIDialogueReturn {
  const {
    role,
    roleName,
    welcomeMessage,
    context,
    workspace,
    initialConversationId,
    sessionId: initialSessionId,
    hostKind = 'electron_workbench',
    attachmentMode = 'isolated',
    capabilityProfile,
    onSessionChange,
    onConversationChange,
  } = options;

  // RoleSession 状态
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [isInitializingSession, setIsInitializingSession] = useState(false);

  // 消息状态
  const [messages, setMessages] = useState<AIMessage[]>([
    { id: 'welcome', role: 'system', content: welcomeMessage, timestamp: new Date() },
  ]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  // 状态
  const [chatStatus, setChatStatus] = useState<ChatStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const statusKind = resolveDialogueStatusKind(chatStatus, statusLoading);
  const isChatReady = statusKind === 'ready';
  const isExplicitlyUnconfigured = statusKind === 'unconfigured';

  // 会话持久化
  const [conversationId, setConversationId] = useState<string | null>(initialConversationId ?? null);
  const [isRestoring, setIsRestoring] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);

  // 防抖定时器
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const messagesRef = useRef(messages);

  // 保持 messages 引用更新
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // 检查角色LLM状态
  const checkStatus = useCallback(async () => {
    try {
      setStatusLoading(true);
      const res = await apiFetch(`/v2/role/${role}/chat/status`);

      if (res.ok) {
        const status = await res.json() as ChatStatus;
        setChatStatus(status);
      } else {
        let errorDetail = '无法获取状态';
        let errorText = '';
        try {
          errorText = await res.text();
          const errorData = JSON.parse(errorText);
          errorDetail = errorData.detail || errorData.error || `HTTP ${res.status}`;
        } catch {
          errorDetail = `HTTP ${res.status}: ${res.statusText}`;
          if (errorText) errorDetail += ` - ${errorText.substring(0, 100)}`;
        }
        setChatStatus({
          ready: false,
          error: errorDetail,
          debug: { httpStatus: res.status, httpStatusText: res.statusText, response: errorText },
        });
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '连接失败';
      setChatStatus({
        ready: false,
        error: errorMessage,
        debug: { exception: String(err) },
      });
    } finally {
      setStatusLoading(false);
    }
  }, [role]);

  // 初始化时检查状态
  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  // 初始化 RoleSession
  useEffect(() => {
    const initSession = async () => {
      if (sessionId) return;
      if (hostKind !== 'electron_workbench' || isInitializingSession) return;

      setIsInitializingSession(true);
      try {
        const normalizedCapabilityProfile = Array.isArray(capabilityProfile)
          ? { capabilities: capabilityProfile }
          : capabilityProfile;
        const res = await apiFetch('/v2/roles/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            role,
            host_kind: hostKind,
            workspace,
            attachment_mode: attachmentMode,
            context_config: context,
            capability_profile: normalizedCapabilityProfile,
          }),
        });
        const data = await res.json();
        if (data.ok && data.session) {
          setSessionId(data.session.id);
          onSessionChange?.(data.session.id);
        }
      } catch (err) {
        devLogger.error('[useAIDialogue] Failed to create session:', err);
      } finally {
        setIsInitializingSession(false);
      }
    };
    void initSession();
  }, [hostKind, role, workspace, context, sessionId, isInitializingSession, capabilityProfile, onSessionChange]);

  // 从已有对话恢复
  useEffect(() => {
    if (!initialConversationId || isRestoring) return;
    setIsRestoring(true);
    getConversation(initialConversationId, true)
      .then((conv) => {
        if (conv.messages?.length) {
          setMessages([
            { id: 'welcome', role: 'system', content: welcomeMessage, timestamp: new Date(conv.created_at) },
            ...conv.messages.map((m) => ({
              id: m.id,
              role: m.role as 'user' | 'assistant' | 'system',
              content: m.content,
              thinking: m.thinking,
              timestamp: new Date(m.created_at),
            })),
          ]);
        }
        setConversationId(conv.id);
      })
      .catch((err) => devLogger.error('恢复对话失败:', err))
      .finally(() => setIsRestoring(false));
  }, [initialConversationId, isRestoring, welcomeMessage]);

  // 自动保存
  useEffect(() => {
    const messagesToSave = messages.filter((m) => m.role !== 'system' && !m.isStreaming);
    if (!messagesToSave.length || !conversationId) return;

    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);

    saveTimeoutRef.current = setTimeout(async () => {
      try {
        await saveFullConversation(
          conversationId,
          role as 'pm' | 'architect' | 'director' | 'qa',
          workspace || '',
          context || {},
          messagesToSave.map((m) => ({ role: m.role as MessageRole, content: m.content, thinking: m.thinking }))
        );
      } catch (err) {
        devLogger.error('自动保存失败:', err);
      }
    }, 2000);

    return () => { if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current); };
  }, [messages, conversationId, role, workspace, context]);

  // 加载历史
  const handleLoadHistory = useCallback(async () => {
    try {
      const result = await listConversations({ role: role as 'pm' | 'architect' | 'director' | 'qa', workspace, limit: 20 });
      setConversations(result.conversations);
    } catch (err) {
      devLogger.error('加载对话列表失败:', err);
    }
  }, [role, workspace]);

  // 处理流式事件
  const handleStreamEvent = useCallback((
    eventType: string,
    eventData: Record<string, unknown> | undefined,
    messageId: string,
    setMsgs: React.Dispatch<React.SetStateAction<AIMessage[]>>
  ) => {
    switch (eventType) {
      case 'thinking_chunk':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, thinking: (m.thinking || '') + ((eventData?.content as string) || ''), statusPhase: 'thinking' as const }
            : m
        ));
        break;
      case 'content_chunk':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, content: m.content + ((eventData?.content as string) || ''), statusPhase: 'executing' as const }
            : m
        ));
        break;
      case 'tool_start':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, toolName: eventData?.tool_name as string, statusPhase: 'tool_running' as const }
            : m
        ));
        break;
      case 'tool_progress':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, progress: eventData?.progress as number, statusPhase: 'tool_running' as const }
            : m
        ));
        break;
      case 'complete':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, content: (eventData?.content ?? eventData?.response ?? eventData?.complete ?? m.content) as string, isStreaming: false, statusPhase: 'completed' as const }
            : m
        ));
        break;
      case 'error':
        setMsgs((prev) => prev.map((m) =>
          m.id === messageId
            ? { ...m, content: `错误: ${eventData?.error ?? eventData?.message ?? '未知错误'}`, isStreaming: false, error: true }
            : m
        ));
        break;
    }
  }, []);

  // 发送消息
  const handleSend = useCallback(async () => {
    if (!inputValue.trim() || isLoading) return;

    if (!isChatReady) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'system',
          content: isExplicitlyUnconfigured
            ? `${roleName} LLM 未就绪: ${chatStatus?.error || '请配置LLM设置'}`
            : `${roleName} 暂时不可用: ${chatStatus?.error || '请重试'}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
      return;
    }

    const userMessage: AIMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: inputValue.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsLoading(true);

    try {
      const history = messagesRef.current
        .filter((m) => m.role !== 'system' && m.id !== userMessage.id)
        .map((m) => ({ role: m.role, content: m.content }));

      const requestBody: Record<string, unknown> = {
        message: userMessage.content,
        context: { ...context, history, conversation_id: conversationId },
      };
      if (sessionId) requestBody.session_id = sessionId;

      const res = await apiFetch(`/v2/role/${role}/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let aiMessage: AIMessage = {
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
                handleStreamEvent(currentEventType, eventData, aiMessage.id, setMessages);
              } catch {
                setMessages((prev) => prev.map((m) =>
                  m.id === aiMessage.id ? { ...m, content: m.content + data } : m
                ));
              }
            }
          }
        }
      }

      setMessages((prev) => prev.map((m) =>
        m.id === aiMessage.id ? { ...m, isStreaming: false } : m
      ));
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: 'system',
          content: `错误: ${err instanceof Error ? err.message : '请求失败'}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [inputValue, isLoading, isChatReady, isExplicitlyUnconfigured, chatStatus?.error, roleName, role, sessionId, context, conversationId, handleStreamEvent]);

  // 键盘事件
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  // 清空消息
  const handleClear = useCallback(() => {
    setMessages([{ id: 'welcome-new', role: 'system', content: `对话已清空。${welcomeMessage}`, timestamp: new Date() }]);
  }, [welcomeMessage]);

  // 切换历史
  const handleToggleHistory = useCallback(() => {
    setShowHistory((prev) => {
      if (!prev) handleLoadHistory();
      return !prev;
    });
  }, [handleLoadHistory]);

  // 新建对话
  const handleNewConversation = useCallback(() => {
    setMessages([{ id: 'welcome-new', role: 'system', content: welcomeMessage, timestamp: new Date() }]);
    setConversationId(null);
    onConversationChange?.(null);
    setShowHistory(false);
  }, [welcomeMessage, onConversationChange]);

  // 选择历史对话
  const handleSelectConversation = useCallback((id: string) => {
    getConversation(id, true)
      .then((conv) => {
        if (conv.messages?.length) {
          setMessages([
            { id: 'welcome', role: 'system', content: welcomeMessage, timestamp: new Date(conv.created_at) },
            ...conv.messages.map((m) => ({
              id: m.id,
              role: m.role as 'user' | 'assistant' | 'system',
              content: m.content,
              thinking: m.thinking,
              timestamp: new Date(m.created_at),
            })),
          ]);
        } else {
          setMessages([{ id: 'welcome-new', role: 'system', content: welcomeMessage, timestamp: new Date() }]);
        }
        setConversationId(conv.id);
        onConversationChange?.(conv.id);
        setShowHistory(false);
      })
      .catch((err) => devLogger.error('加载对话失败:', err));
  }, [welcomeMessage, onConversationChange]);

  return {
    messages,
    inputValue,
    setInputValue,
    isLoading,
    chatStatus,
    statusLoading,
    statusKind,
    isChatReady,
    isExplicitlyUnconfigured,
    conversationId,
    showHistory,
    conversations,
    configuredProviderLabel: chatStatus?.role_config?.provider_id || roleName,
    configuredModelLabel: chatStatus?.role_config?.model || 'Model',
    checkStatus,
    handleSend,
    handleClear,
    handleKeyDown,
    handleToggleHistory,
    handleNewConversation,
    handleSelectConversation,
    handleLoadHistory,
  };
}
