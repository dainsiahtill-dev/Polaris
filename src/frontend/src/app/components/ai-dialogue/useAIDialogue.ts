/**
 * AI 对话核心 Hook
 *
 * 处理对话状态、消息、流式事件等核心逻辑
 */

import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { apiFetch } from '@/api';
import { useRuntimeTransport } from '@/runtime/transport';
import { devLogger } from '@/app/utils/devLogger';
import {
  getConversation,
  listConversations,
  saveFullConversation,
  type Conversation,
  type DialogueRole,
  type MessageRole,
} from '@/services/conversationApi';
import {
  attachRoleSession,
  createRoleSession,
  detachRoleSession,
  exportRoleSessionSnapshot,
  exportRoleSessionToWorkflow,
  getRoleCapabilities,
  getRoleSession,
  listRoleSessionMessages,
  listRoleSessions,
  readRoleSessionMemoryArtifact,
  readRoleSessionMemoryEpisode,
  readRoleSessionMemoryState,
  resolveRoleCapabilities,
  searchRoleSessionMemory,
  type RoleSessionDetailItem,
  type RoleSessionListItem,
  type RoleSessionMemoryDetailItem,
  type RoleSessionMemoryItem,
  type RoleSessionMessageItem,
  type RoleSessionSnapshotExportFormat,
} from '@/services/roleSessionService';
import { resolveDialogueStatusKind, type DialogueChatStatus } from './chatStatusState';
import type { AIMessage } from './AIMessageList';

export type {
  RoleSessionArtifactItem,
  RoleSessionAuditEventItem,
  RoleSessionDetailItem,
  RoleSessionListItem,
  RoleSessionMemoryDetailItem,
  RoleSessionMemoryItem,
  RoleSessionSnapshotExportFormat,
} from '@/services/roleSessionService';

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

interface JetstreamChatStartResponse {
  session_id?: string;
  status?: string;
  channel?: string;
  subject?: string;
  transport?: string;
}

function appendWorkspaceQuery(path: string, workspace?: string): string {
  const value = String(workspace || '').trim();
  if (!value) {
    return path;
  }
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}workspace=${encodeURIComponent(value)}`;
}

export type WorkflowExportTarget = 'pm' | 'director' | 'factory';

export interface WorkflowExportStatus {
  kind: 'idle' | 'success' | 'error';
  message: string;
  runId?: string;
  artifactCount?: number;
  messageCount?: number;
}

export interface RoleSessionSnapshotExportStatus {
  kind: 'idle' | 'success' | 'error';
  message: string;
  format?: RoleSessionSnapshotExportFormat;
}

export interface RoleSessionDetachStatus {
  kind: 'idle' | 'success' | 'error';
  message: string;
}

function normalizeAttachmentId(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'bigint') return String(value);
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return normalizeAttachmentId(record.id ?? record.task_id);
  }
  return '';
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
  /** 附着的工作流 Run ID */
  attachedRunId?: string;
  /** 附着的任务 ID */
  attachedTaskId?: unknown;
  /** 能力配置 */
  capabilityProfile?: Record<string, unknown> | string[];
  /** 显式导出到工作流的目标 */
  workflowExportTarget?: WorkflowExportTarget;
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
  sessionId: string | null;
  isInitializingSession: boolean;
  sessionError: string;
  isExportingWorkflow: boolean;
  workflowExportStatus: WorkflowExportStatus;
  showRoleSessions: boolean;
  roleSessions: RoleSessionListItem[];
  isLoadingRoleSessions: boolean;
  roleSessionListError: string;
  showRoleSessionEvidence: boolean;
  showRoleSessionMemory: boolean;
  roleSessionMemoryQuery: string;
  roleSessionMemoryItems: RoleSessionMemoryItem[];
  isLoadingRoleSessionMemory: boolean;
  roleSessionMemoryError: string;
  roleSessionMemoryDetail: RoleSessionMemoryDetailItem | null;
  isLoadingRoleSessionMemoryDetail: boolean;
  roleSessionMemoryDetailError: string;
  showRoleSessionSnapshotExport: boolean;
  roleSessionSnapshotExportFormat: RoleSessionSnapshotExportFormat;
  roleSessionSnapshotExportPayload: unknown;
  isExportingRoleSessionSnapshot: boolean;
  roleSessionSnapshotExportStatus: RoleSessionSnapshotExportStatus;
  roleCapabilities: string[];
  isLoadingRoleCapabilities: boolean;
  roleCapabilitiesError: string;
  activeRoleSessionDetail: RoleSessionDetailItem | null;
  isLoadingRoleSessionDetail: boolean;
  roleSessionDetailError: string;
  isDetachingRoleSession: boolean;
  roleSessionDetachStatus: RoleSessionDetachStatus;
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
  handleNewRoleSession: () => void;
  handleLoadRoleSessions: () => Promise<void>;
  handleToggleRoleSessions: () => void;
  handleSelectRoleSession: (id: string) => Promise<void>;
  handleDetachRoleSession: () => Promise<void>;
  handleToggleRoleSessionEvidence: () => void;
  setRoleSessionMemoryQuery: (value: string) => void;
  handleLoadRoleSessionMemory: (queryOverride?: string) => Promise<void>;
  handleToggleRoleSessionMemory: () => void;
  handleReadRoleSessionMemoryItem: (item: RoleSessionMemoryItem) => Promise<void>;
  setRoleSessionSnapshotExportFormat: (format: RoleSessionSnapshotExportFormat) => void;
  handleExportRoleSessionSnapshot: (format?: RoleSessionSnapshotExportFormat) => Promise<void>;
  handleToggleRoleSessionSnapshotExport: () => void;
  handleExportToWorkflow: () => Promise<void>;
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
    attachedRunId,
    attachedTaskId,
    capabilityProfile,
    workflowExportTarget,
    onSessionChange,
    onConversationChange,
  } = options;
  const normalizedAttachedTaskId = useMemo(() => normalizeAttachmentId(attachedTaskId), [attachedTaskId]);

  // RoleSession 状态
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId ?? null);
  const [isInitializingSession, setIsInitializingSession] = useState(false);
  const [sessionError, setSessionError] = useState('');
  const [isExportingWorkflow, setIsExportingWorkflow] = useState(false);
  const [workflowExportStatus, setWorkflowExportStatus] = useState<WorkflowExportStatus>({
    kind: 'idle',
    message: '',
  });
  const [showRoleSessions, setShowRoleSessions] = useState(false);
  const [roleSessions, setRoleSessions] = useState<RoleSessionListItem[]>([]);
  const [isLoadingRoleSessions, setIsLoadingRoleSessions] = useState(false);
  const [roleSessionListError, setRoleSessionListError] = useState('');
  const [showRoleSessionEvidence, setShowRoleSessionEvidence] = useState(false);
  const [showRoleSessionMemory, setShowRoleSessionMemory] = useState(false);
  const [roleSessionMemoryQuery, setRoleSessionMemoryQuery] = useState('');
  const [roleSessionMemoryItems, setRoleSessionMemoryItems] = useState<RoleSessionMemoryItem[]>([]);
  const [isLoadingRoleSessionMemory, setIsLoadingRoleSessionMemory] = useState(false);
  const [roleSessionMemoryError, setRoleSessionMemoryError] = useState('');
  const [roleSessionMemoryDetail, setRoleSessionMemoryDetail] = useState<RoleSessionMemoryDetailItem | null>(null);
  const [isLoadingRoleSessionMemoryDetail, setIsLoadingRoleSessionMemoryDetail] = useState(false);
  const [roleSessionMemoryDetailError, setRoleSessionMemoryDetailError] = useState('');
  const [showRoleSessionSnapshotExport, setShowRoleSessionSnapshotExport] = useState(false);
  const [roleSessionSnapshotExportFormat, setRoleSessionSnapshotExportFormat] =
    useState<RoleSessionSnapshotExportFormat>('json');
  const [roleSessionSnapshotExportPayload, setRoleSessionSnapshotExportPayload] = useState<unknown>(null);
  const [isExportingRoleSessionSnapshot, setIsExportingRoleSessionSnapshot] = useState(false);
  const [roleSessionSnapshotExportStatus, setRoleSessionSnapshotExportStatus] =
    useState<RoleSessionSnapshotExportStatus>({ kind: 'idle', message: '' });
  const [roleCapabilities, setRoleCapabilities] = useState<string[]>([]);
  const [isLoadingRoleCapabilities, setIsLoadingRoleCapabilities] = useState(false);
  const [roleCapabilitiesError, setRoleCapabilitiesError] = useState('');
  const [activeRoleSessionDetail, setActiveRoleSessionDetail] = useState<RoleSessionDetailItem | null>(null);
  const [isLoadingRoleSessionDetail, setIsLoadingRoleSessionDetail] = useState(false);
  const [roleSessionDetailError, setRoleSessionDetailError] = useState('');
  const [isDetachingRoleSession, setIsDetachingRoleSession] = useState(false);
  const [roleSessionDetachStatus, setRoleSessionDetachStatus] = useState<RoleSessionDetachStatus>({
    kind: 'idle',
    message: '',
  });
  const makeAttachmentKey = useCallback((nextSessionId: string | null = sessionId) => [
    nextSessionId || '',
    attachmentMode,
    attachedRunId || '',
    normalizedAttachedTaskId,
  ].join('|'), [sessionId, attachmentMode, attachedRunId, normalizedAttachedTaskId]);
  const getDefaultMemoryQuery = useCallback(() => String(
    normalizedAttachedTaskId || attachedRunId || roleName || role,
  ).trim(), [normalizedAttachedTaskId, attachedRunId, roleName, role]);
  const dialogueWorkspace = String(workspace || (typeof context?.workspace === 'string' ? context.workspace : '') || '').trim();

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
  const transport = useRuntimeTransport();

  // 防抖定时器
  const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const messagesRef = useRef(messages);
  const lastAttachmentKeyRef = useRef('');
  const detachedAttachmentKeyRef = useRef('');
  const streamUnsubscribeRef = useRef<(() => void) | null>(null);
  const streamHandlerUnregisterRef = useRef<(() => void) | null>(null);

  const cleanupActiveStream = useCallback(() => {
    if (streamUnsubscribeRef.current) {
      try { streamUnsubscribeRef.current(); } catch { /* noop */ }
      streamUnsubscribeRef.current = null;
    }
    if (streamHandlerUnregisterRef.current) {
      try { streamHandlerUnregisterRef.current(); } catch { /* noop */ }
      streamHandlerUnregisterRef.current = null;
    }
  }, []);

  // 保持 messages 引用更新
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => () => cleanupActiveStream(), [cleanupActiveStream]);

  useEffect(() => {
    setWorkflowExportStatus({ kind: 'idle', message: '' });
    setRoleSessionMemoryItems([]);
    setRoleSessionMemoryError('');
    setRoleSessionMemoryDetail(null);
    setRoleSessionMemoryDetailError('');
    setRoleSessionSnapshotExportPayload(null);
    setRoleSessionSnapshotExportStatus({ kind: 'idle', message: '' });
    setActiveRoleSessionDetail(null);
    setRoleSessionDetailError('');
    setRoleSessionDetachStatus({ kind: 'idle', message: '' });
  }, [sessionId]);

  // 检查角色LLM状态
  const checkStatus = useCallback(async () => {
    try {
      setStatusLoading(true);
      const res = await apiFetch(appendWorkspaceQuery(`/v2/role/${role}/chat/status`, dialogueWorkspace));

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
  }, [role, dialogueWorkspace]);

  // 初始化时检查状态
  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  useEffect(() => {
    let cancelled = false;
    const loadRoleCapabilities = async () => {
      setIsLoadingRoleCapabilities(true);
      setRoleCapabilitiesError('');
      try {
        const result = await getRoleCapabilities(role, hostKind);
        if (!result.ok || !result.data || result.data.ok === false) {
          throw new Error(result.error || result.data?.error || result.data?.detail || result.data?.message || '角色能力加载失败');
        }
        const nextCapabilities = resolveRoleCapabilities(result.data, hostKind);
        if (!cancelled) {
          setRoleCapabilities(nextCapabilities);
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : '角色能力加载失败';
          setRoleCapabilities([]);
          setRoleCapabilitiesError(message);
          devLogger.error('[useAIDialogue] Failed to load role capabilities:', err);
        }
      } finally {
        if (!cancelled) {
          setIsLoadingRoleCapabilities(false);
        }
      }
    };

    void loadRoleCapabilities();
    return () => {
      cancelled = true;
    };
  }, [role, hostKind]);

  useEffect(() => {
    if (!sessionId) {
      setActiveRoleSessionDetail(null);
      setRoleSessionDetailError('');
      setIsLoadingRoleSessionDetail(false);
      return;
    }

    let cancelled = false;
    const loadRoleSessionDetail = async () => {
      setIsLoadingRoleSessionDetail(true);
      setRoleSessionDetailError('');
      try {
        const result = await getRoleSession(sessionId);
        if (!result.ok || !result.data) {
          throw new Error(result.error || 'RoleSession 详情加载失败');
        }
        if (!cancelled) {
          setActiveRoleSessionDetail(result.data);
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'RoleSession 详情加载失败';
          setActiveRoleSessionDetail(null);
          setRoleSessionDetailError(message);
          devLogger.error('[useAIDialogue] Failed to load role session detail:', err);
        }
      } finally {
        if (!cancelled) {
          setIsLoadingRoleSessionDetail(false);
        }
      }
    };

    void loadRoleSessionDetail();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // 初始化 RoleSession
  useEffect(() => {
    const initSession = async () => {
      if (sessionId) return;
      if (hostKind !== 'electron_workbench' || isInitializingSession) return;

      setIsInitializingSession(true);
      setSessionError('');
      try {
        const normalizedCapabilityProfile = Array.isArray(capabilityProfile)
          ? { capabilities: capabilityProfile }
          : capabilityProfile;
        const result = await createRoleSession({
          role,
          host_kind: hostKind,
          workspace,
          attachment_mode: attachmentMode,
          context_config: context,
          capability_profile: normalizedCapabilityProfile,
        });
        const nextSessionId = typeof result.data?.id === 'string' ? result.data.id : '';
        if (result.ok && nextSessionId) {
          lastAttachmentKeyRef.current = '';
          detachedAttachmentKeyRef.current = '';
          setSessionId(nextSessionId);
          onSessionChange?.(nextSessionId);
        } else {
          throw new Error(result.error || 'RoleSession create response missing session id');
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'RoleSession 创建失败';
        setSessionError(message);
        devLogger.error('[useAIDialogue] Failed to create session:', err);
      } finally {
        setIsInitializingSession(false);
      }
    };
    void initSession();
  }, [hostKind, role, workspace, context, sessionId, isInitializingSession, attachmentMode, capabilityProfile, onSessionChange]);

  // 将桌面对话会话附着到当前工作流/任务上下文。
  useEffect(() => {
    if (!sessionId || attachmentMode === 'isolated') return;
    if (!attachedRunId && !normalizedAttachedTaskId) return;

    const attachmentKey = makeAttachmentKey(sessionId);
    if (detachedAttachmentKeyRef.current === attachmentKey) return;
    if (lastAttachmentKeyRef.current === attachmentKey) return;
    lastAttachmentKeyRef.current = attachmentKey;

    const attachSession = async () => {
      try {
        const result = await attachRoleSession(sessionId, {
          run_id: attachedRunId || null,
          task_id: normalizedAttachedTaskId || null,
          mode: attachmentMode,
          note: `${roleName} desktop dialogue attachment`,
        });
        if (!result.ok) {
          lastAttachmentKeyRef.current = '';
          devLogger.error('[useAIDialogue] Failed to attach session:', result.error);
        }
      } catch (err) {
        lastAttachmentKeyRef.current = '';
        devLogger.error('[useAIDialogue] Failed to attach session:', err);
      }
    };

    void attachSession();
  }, [sessionId, attachmentMode, attachedRunId, normalizedAttachedTaskId, roleName, makeAttachmentKey]);

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
          role as DialogueRole,
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
      const result = await listConversations({ role: role as DialogueRole, workspace, limit: 20 });
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

      const runtimeContext = { ...context, workspace: dialogueWorkspace || context?.workspace, history, conversation_id: conversationId };
      cleanupActiveStream();

      if (!transport.connected) {
        transport.reconnect();
        throw new Error('runtime transport not connected');
      }

      const streamPath = sessionId
        ? `/v2/roles/sessions/${encodeURIComponent(sessionId)}/messages/jetstream`
        : appendWorkspaceQuery(`/v2/role/${role}/chat/jetstream`, dialogueWorkspace);
      const requestBody: Record<string, unknown> = sessionId
        ? {
          role: 'user',
          content: userMessage.content,
          meta: {
            context: runtimeContext,
            conversation_id: conversationId,
          },
        }
        : {
          message: userMessage.content,
          context: runtimeContext,
        };
      const aiMessage: AIMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '',
        thinking: '',
        timestamp: new Date(),
        isStreaming: true,
        statusPhase: 'thinking',
      };

      setMessages((prev) => [...prev, aiMessage]);

      const res = await apiFetch(streamPath, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const startPayload = (await res.json()) as JetstreamChatStartResponse;
      const channel = typeof startPayload.channel === 'string' ? startPayload.channel : '';
      if (!channel) {
        throw new Error('missing JetStream channel');
      }

      streamUnsubscribeRef.current = transport.subscribeChannels([{ channel, tailLines: 0 }]);
      streamHandlerUnregisterRef.current = transport.registerMessageHandler((raw: unknown) => {
        const msg = raw as Record<string, unknown>;
        const event = msg?.type === 'EVENT'
          ? msg.event as Record<string, unknown> | undefined
          : msg;
        if (!event || event.channel !== channel) return;
        const payload = event.payload as Record<string, unknown> | undefined;
        const eventType = String(payload?.type || 'message');
        const eventData = payload?.data && typeof payload.data === 'object'
          ? payload.data as Record<string, unknown>
          : {};

        handleStreamEvent(eventType, eventData, aiMessage.id, setMessages);
        if (eventType === 'complete' || eventType === 'error') {
          cleanupActiveStream();
          setIsLoading(false);
        }
      });
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
      setIsLoading(false);
    }
  }, [inputValue, isLoading, isChatReady, isExplicitlyUnconfigured, chatStatus?.error, roleName, role, sessionId, context, conversationId, dialogueWorkspace, transport, cleanupActiveStream, handleStreamEvent]);

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

  const handleLoadRoleSessions = useCallback(async () => {
    setIsLoadingRoleSessions(true);
    setRoleSessionListError('');
    try {
      const result = await listRoleSessions({
        role,
        hostKind,
        workspace,
        limit: 20,
      });
      if (!result.ok || !Array.isArray(result.data)) {
        throw new Error(result.error || 'RoleSession 列表加载失败');
      }

      setRoleSessions(result.data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 列表加载失败';
      setRoleSessionListError(message);
      devLogger.error('[useAIDialogue] Failed to load role sessions:', err);
    } finally {
      setIsLoadingRoleSessions(false);
    }
  }, [role, hostKind, workspace]);

  // 新建 RoleSession
  const handleNewRoleSession = useCallback(() => {
    lastAttachmentKeyRef.current = '';
    detachedAttachmentKeyRef.current = '';
    setSessionError('');
    setWorkflowExportStatus({ kind: 'idle', message: '' });
    setRoleSessionDetachStatus({ kind: 'idle', message: '' });
    setSessionId(null);
    setShowRoleSessions(false);
    setShowRoleSessionEvidence(false);
    setShowRoleSessionMemory(false);
    setShowRoleSessionSnapshotExport(false);
    onSessionChange?.(null);
    setMessages([{ id: 'welcome-new-session', role: 'system', content: welcomeMessage, timestamp: new Date() }]);
  }, [welcomeMessage, onSessionChange]);

  const handleToggleRoleSessions = useCallback(() => {
    setShowRoleSessions((current) => {
      const next = !current;
      if (next) {
        void handleLoadRoleSessions();
      }
      return next;
    });
  }, [handleLoadRoleSessions]);

  const handleSelectRoleSession = useCallback(async (id: string) => {
    const nextSessionId = String(id || '').trim();
    if (!nextSessionId) return;

    lastAttachmentKeyRef.current = '';
    detachedAttachmentKeyRef.current = '';
    setSessionError('');
    setWorkflowExportStatus({ kind: 'idle', message: '' });
    setRoleSessionDetachStatus({ kind: 'idle', message: '' });
    setSessionId(nextSessionId);
    setShowRoleSessions(false);
    setShowRoleSessionEvidence(false);
    setShowRoleSessionMemory(false);
    setShowRoleSessionSnapshotExport(false);
    onSessionChange?.(nextSessionId);
    setIsLoading(true);

    try {
      const result = await listRoleSessionMessages(nextSessionId, { limit: 100, offset: 0 });
      if (!result.ok || !Array.isArray(result.data)) {
        throw new Error(result.error || 'RoleSession 消息恢复失败');
      }

      const restoredMessages: AIMessage[] = [];
      for (const message of result.data) {
        if (!message || typeof message !== 'object') continue;
        const record = message as RoleSessionMessageItem;
        const roleValue = String(record.role || 'system');
        const normalizedRole: AIMessage['role'] = roleValue === 'user' || roleValue === 'assistant' || roleValue === 'system'
          ? roleValue
          : 'system';
        const createdAt = typeof record.created_at === 'string' ? Date.parse(record.created_at) : Number.NaN;
        restoredMessages.push({
          id: String(record.id || `${nextSessionId}-${Date.now()}`),
          role: normalizedRole,
          content: String(record.content || ''),
          thinking: typeof record.thinking === 'string' ? record.thinking : undefined,
          timestamp: Number.isFinite(createdAt) ? new Date(createdAt) : new Date(),
        });
      }

      setMessages([
        {
          id: 'welcome-restored-session',
          role: 'system',
          content: `已恢复 RoleSession ${nextSessionId}。${welcomeMessage}`,
          timestamp: new Date(),
        },
        ...restoredMessages,
      ]);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 消息恢复失败';
      setSessionError(message);
      devLogger.error('[useAIDialogue] Failed to restore role session messages:', err);
      setMessages([
        {
          id: 'restore-session-error',
          role: 'system',
          content: `错误: ${message}`,
          timestamp: new Date(),
          error: true,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }, [welcomeMessage, onSessionChange]);

  const handleToggleRoleSessionEvidence = useCallback(() => {
    setShowRoleSessionEvidence((current) => !current);
  }, []);

  const handleLoadRoleSessionMemory = useCallback(async (queryOverride?: string) => {
    if (!sessionId) {
      setRoleSessionMemoryError('RoleSession 尚未创建');
      return;
    }

    const query = String(queryOverride ?? roleSessionMemoryQuery ?? '').trim() || getDefaultMemoryQuery();
    if (!query) {
      setRoleSessionMemoryError('缺少记忆检索关键词');
      return;
    }

    setRoleSessionMemoryQuery(query);
    setIsLoadingRoleSessionMemory(true);
    setRoleSessionMemoryError('');
    try {
      const result = await searchRoleSessionMemory(sessionId, query, { limit: 8 });
      if (!result.ok || !Array.isArray(result.data)) {
        throw new Error(result.error || 'RoleSession 记忆检索失败');
      }

      setRoleSessionMemoryItems(result.data);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 记忆检索失败';
      setRoleSessionMemoryError(message);
      devLogger.error('[useAIDialogue] Failed to load role session memory:', err);
    } finally {
      setIsLoadingRoleSessionMemory(false);
    }
  }, [sessionId, roleSessionMemoryQuery, getDefaultMemoryQuery]);

  const handleToggleRoleSessionMemory = useCallback(() => {
    setShowRoleSessionMemory((current) => {
      const next = !current;
      if (next && roleSessionMemoryItems.length === 0 && !isLoadingRoleSessionMemory) {
        void handleLoadRoleSessionMemory();
      }
      return next;
    });
  }, [handleLoadRoleSessionMemory, isLoadingRoleSessionMemory, roleSessionMemoryItems.length]);

  const handleReadRoleSessionMemoryItem = useCallback(async (item: RoleSessionMemoryItem) => {
    if (!sessionId) {
      setRoleSessionMemoryDetailError('RoleSession 尚未创建');
      return;
    }

    const kind = String(item.kind || '').trim().toLowerCase();
    const rawId = String(item.id || item.path || item.entity || '').trim();
    if (!rawId) {
      setRoleSessionMemoryDetail({
        id: 'inline',
        kind: kind || 'memory',
        payload: item,
      });
      return;
    }

    setIsLoadingRoleSessionMemoryDetail(true);
    setRoleSessionMemoryDetailError('');
    try {
      let detailResult: { ok: boolean; data?: unknown; error?: string };
      if (kind === 'artifact') {
        detailResult = await readRoleSessionMemoryArtifact(sessionId, rawId);
      } else if (kind === 'episode') {
        detailResult = await readRoleSessionMemoryEpisode(sessionId, rawId);
      } else if (kind === 'state') {
        const metadataPath = typeof item.metadata?.path === 'string' ? item.metadata.path : '';
        const statePath = String(item.path || item.entity || metadataPath || rawId).trim();
        detailResult = await readRoleSessionMemoryState(sessionId, statePath);
      } else {
        setRoleSessionMemoryDetail({
          id: rawId,
          kind: kind || 'memory',
          payload: item,
        });
        return;
      }

      if (!detailResult.ok) {
        throw new Error(detailResult.error || 'RoleSession 记忆详情读取失败');
      }

      setRoleSessionMemoryDetail({
        id: rawId,
        kind: kind || 'memory',
        payload: detailResult.data,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 记忆详情读取失败';
      setRoleSessionMemoryDetailError(message);
      devLogger.error('[useAIDialogue] Failed to read role session memory detail:', err);
    } finally {
      setIsLoadingRoleSessionMemoryDetail(false);
    }
  }, [sessionId]);

  const handleDetachRoleSession = useCallback(async () => {
    if (!sessionId || isDetachingRoleSession) return;

    const attachmentKey = makeAttachmentKey(sessionId);
    setIsDetachingRoleSession(true);
    setRoleSessionDetachStatus({ kind: 'idle', message: '' });
    try {
      const result = await detachRoleSession(sessionId);
      if (!result.ok) {
        throw new Error(result.error || 'RoleSession 解除附着失败');
      }

      detachedAttachmentKeyRef.current = attachmentKey;
      lastAttachmentKeyRef.current = attachmentKey;
      if (result.data) {
        setActiveRoleSessionDetail(result.data);
      }
      setRoleSessionDetachStatus({ kind: 'success', message: '已解除工作流附着' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 解除附着失败';
      setRoleSessionDetachStatus({ kind: 'error', message });
      devLogger.error('[useAIDialogue] Failed to detach session:', err);
    } finally {
      setIsDetachingRoleSession(false);
    }
  }, [sessionId, isDetachingRoleSession, makeAttachmentKey]);

  const handleExportRoleSessionSnapshot = useCallback(async (
    format: RoleSessionSnapshotExportFormat = roleSessionSnapshotExportFormat,
  ) => {
    if (!sessionId || isExportingRoleSessionSnapshot) return;

    setRoleSessionSnapshotExportFormat(format);
    setIsExportingRoleSessionSnapshot(true);
    setRoleSessionSnapshotExportStatus({ kind: 'idle', message: '', format });
    try {
      const result = await exportRoleSessionSnapshot(sessionId, {
        include_messages: true,
        format,
      });
      if (!result.ok) {
        throw new Error(result.error || 'RoleSession 快照导出失败');
      }

      setRoleSessionSnapshotExportPayload(result.data ?? null);
      setRoleSessionSnapshotExportStatus({
        kind: 'success',
        message: format === 'markdown' ? '已生成 Markdown 快照' : '已生成 JSON 快照',
        format,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'RoleSession 快照导出失败';
      setRoleSessionSnapshotExportPayload(null);
      setRoleSessionSnapshotExportStatus({ kind: 'error', message, format });
      devLogger.error('[useAIDialogue] Failed to export role session snapshot:', err);
    } finally {
      setIsExportingRoleSessionSnapshot(false);
    }
  }, [sessionId, roleSessionSnapshotExportFormat, isExportingRoleSessionSnapshot]);

  const handleToggleRoleSessionSnapshotExport = useCallback(() => {
    setShowRoleSessionSnapshotExport((current) => {
      const next = !current;
      if (next && !roleSessionSnapshotExportPayload && !isExportingRoleSessionSnapshot) {
        void handleExportRoleSessionSnapshot(roleSessionSnapshotExportFormat);
      }
      return next;
    });
  }, [
    handleExportRoleSessionSnapshot,
    isExportingRoleSessionSnapshot,
    roleSessionSnapshotExportFormat,
    roleSessionSnapshotExportPayload,
  ]);

  const handleExportToWorkflow = useCallback(async () => {
    if (!sessionId || !workflowExportTarget || isExportingWorkflow) return;

    setIsExportingWorkflow(true);
    setWorkflowExportStatus({ kind: 'idle', message: '' });
    try {
      const result = await exportRoleSessionToWorkflow(sessionId, {
        target: workflowExportTarget,
        export_kind: 'session_bundle',
        include_audit_log: true,
      });
      if (!result.ok || !result.data) {
        throw new Error(result.error || '导出到工作流失败');
      }

      const runId = typeof result.data.run_id === 'string' ? result.data.run_id : '';
      if (!runId) {
        throw new Error('Workflow export response missing run_id');
      }

      const artifactCount = Number(result.data.artifact_count ?? 0);
      const messageCount = Number(result.data.message_count ?? 0);
      setWorkflowExportStatus({
        kind: 'success',
        message: `已导出到 ${workflowExportTarget.toUpperCase()} 工作流`,
        runId,
        artifactCount: Number.isFinite(artifactCount) ? artifactCount : 0,
        messageCount: Number.isFinite(messageCount) ? messageCount : 0,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : '导出到工作流失败';
      setWorkflowExportStatus({ kind: 'error', message });
      devLogger.error('[useAIDialogue] Failed to export session to workflow:', err);
    } finally {
      setIsExportingWorkflow(false);
    }
  }, [sessionId, workflowExportTarget, isExportingWorkflow]);

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
    sessionId,
    isInitializingSession,
    sessionError,
    isExportingWorkflow,
    workflowExportStatus,
    showRoleSessions,
    roleSessions,
    isLoadingRoleSessions,
    roleSessionListError,
    showRoleSessionEvidence,
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
    configuredProviderLabel: chatStatus?.role_config?.provider_id || roleName,
    configuredModelLabel: chatStatus?.role_config?.model || 'Model',
    checkStatus,
    handleSend,
    handleClear,
    handleNewRoleSession,
    handleLoadRoleSessions,
    handleToggleRoleSessions,
    handleSelectRoleSession,
    handleDetachRoleSession,
    handleToggleRoleSessionEvidence,
    setRoleSessionMemoryQuery,
    handleLoadRoleSessionMemory,
    handleToggleRoleSessionMemory,
    handleReadRoleSessionMemoryItem,
    setRoleSessionSnapshotExportFormat,
    handleExportRoleSessionSnapshot,
    handleToggleRoleSessionSnapshotExport,
    handleExportToWorkflow,
    handleKeyDown,
    handleToggleHistory,
    handleNewConversation,
    handleSelectConversation,
    handleLoadHistory,
  };
}
