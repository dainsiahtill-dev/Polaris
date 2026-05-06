/**
 * useV2Api - React Hooks for V2 API Services
 *
 * Provides typed React hooks wrapping the v2 API service layer:
 * - useRoleChat: non-streaming role chat
 * - useRoleChatStatus: role chat readiness status
 * - useConversations: conversation CRUD
 * - useFactoryRuns: factory run monitoring
 * - useLLMConfig: LLM configuration
 * - useSettings: settings management
 * - useHealth: health checks
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import {
  roleChatService,
  conversationV2Service,
  factoryRunV2Service,
  llmConfigService,
  settingsV2Service,
  healthV2Service,
} from '@/services/api';
import type { ApiResult } from '@/services/api.types';
import type {
  RoleChatRequest,
  RoleChatResponse,
  RoleChatStatusResponse,
  ConversationV2,
  ConversationMessageV2,
  ConversationListResponseV2,
  CreateConversationRequestV2,
  AddConversationMessageRequestV2,
  FactoryRunEventsV2Response,
  FactoryRunAuditBundleV2Response,
  LLMConfigResponse,
  LLMStatusResponse,
  LLMProviderListResponse,
  LLMConfigMigrateRequest,
  LLMConfigMigrateResponse,
  SettingsV2Response,
  SettingsV2UpdateRequest,
  HealthV2Response,
} from '@/services/api.types';

// ============================================================================
// useRoleChat
// ============================================================================

export interface UseRoleChatResult {
  response: string;
  thinking: string;
  loading: boolean;
  error: string;
  sendMessage: (message: string, context?: Record<string, unknown>) => Promise<void>;
  reset: () => void;
}

export function useRoleChat(role: string): UseRoleChatResult {
  const [response, setResponse] = useState('');
  const [thinking, setThinking] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const sendMessage = useCallback(
    async (message: string, context?: Record<string, unknown>) => {
      setLoading(true);
      setError('');
      setResponse('');
      setThinking('');
      try {
        const request: RoleChatRequest = { message, context };
        const result: ApiResult<RoleChatResponse> = await roleChatService.chat(role, request);
        if (result.ok && result.data) {
          setResponse(result.data.response);
          setThinking(result.data.thinking ?? '');
        } else {
          setError(result.error ?? 'Role chat failed');
        }
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    },
    [role]
  );

  const reset = useCallback(() => {
    setResponse('');
    setThinking('');
    setError('');
  }, []);

  return { response, thinking, loading, error, sendMessage, reset };
}

// ============================================================================
// useRoleChatStatus
// ============================================================================

export interface UseRoleChatStatusResult {
  status: RoleChatStatusResponse | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
}

export function useRoleChatStatus(role: string): UseRoleChatStatusResult {
  const [status, setStatus] = useState<RoleChatStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await roleChatService.getStatus(role);
      if (result.ok && result.data) {
        setStatus(result.data);
      } else {
        setError(result.error ?? 'Failed to load role chat status');
        setStatus(null);
      }
    } catch (e) {
      setError(String(e));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [role]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { status, loading, error, refresh };
}

// ============================================================================
// useConversations
// ============================================================================

export interface UseConversationsResult {
  conversations: ConversationV2[];
  total: number;
  loading: boolean;
  error: string;
  list: (params?: {
    role?: string;
    workspace?: string;
    limit?: number;
    offset?: number;
  }) => Promise<void>;
  create: (request: CreateConversationRequestV2) => Promise<ConversationV2 | null>;
  getMessages: (
    conversationId: string,
    params?: { limit?: number; offset?: number }
  ) => Promise<ConversationMessageV2[]>;
  addMessage: (
    conversationId: string,
    request: AddConversationMessageRequestV2
  ) => Promise<ConversationMessageV2 | null>;
}

export function useConversations(): UseConversationsResult {
  const [conversations, setConversations] = useState<ConversationV2[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const list = useCallback(async (params?: {
    role?: string;
    workspace?: string;
    limit?: number;
    offset?: number;
  }) => {
    setLoading(true);
    setError('');
    try {
      const result = await conversationV2Service.list(params);
      if (result.ok && result.data) {
        setConversations(result.data.conversations ?? []);
        setTotal(result.data.total ?? 0);
      } else {
        setError(result.error ?? 'Failed to list conversations');
        setConversations([]);
        setTotal(0);
      }
    } catch (e) {
      setError(String(e));
      setConversations([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  const create = useCallback(async (request: CreateConversationRequestV2): Promise<ConversationV2 | null> => {
    setLoading(true);
    setError('');
    try {
      const result = await conversationV2Service.create(request);
      if (result.ok && result.data) {
        setConversations((prev) => [result.data!, ...prev]);
        setTotal((prev) => prev + 1);
        return result.data;
      }
      setError(result.error ?? 'Failed to create conversation');
      return null;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const getMessages = useCallback(async (
    conversationId: string,
    params?: { limit?: number; offset?: number }
  ): Promise<ConversationMessageV2[]> => {
    setLoading(true);
    setError('');
    try {
      const result = await conversationV2Service.getMessages(conversationId, params);
      if (result.ok && result.data) {
        return result.data;
      }
      setError(result.error ?? 'Failed to get conversation messages');
      return [];
    } catch (e) {
      setError(String(e));
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const addMessage = useCallback(async (
    conversationId: string,
    request: AddConversationMessageRequestV2
  ): Promise<ConversationMessageV2 | null> => {
    setLoading(true);
    setError('');
    try {
      const result = await conversationV2Service.addMessage(conversationId, request);
      if (result.ok && result.data) {
        return result.data;
      }
      setError(result.error ?? 'Failed to add conversation message');
      return null;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { conversations, total, loading, error, list, create, getMessages, addMessage };
}

// ============================================================================
// useFactoryRuns
// ============================================================================

export interface UseFactoryRunsResult {
  events: FactoryRunEventsV2Response | null;
  auditBundle: FactoryRunAuditBundleV2Response | null;
  loading: boolean;
  error: string;
  fetchEvents: (runId: string, params?: { limit?: number; offset?: number }) => Promise<void>;
  fetchAuditBundle: (runId: string) => Promise<void>;
}

export function useFactoryRuns(): UseFactoryRunsResult {
  const [events, setEvents] = useState<FactoryRunEventsV2Response | null>(null);
  const [auditBundle, setAuditBundle] = useState<FactoryRunAuditBundleV2Response | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const fetchEvents = useCallback(async (runId: string, params?: { limit?: number; offset?: number }) => {
    setLoading(true);
    setError('');
    try {
      const result = await factoryRunV2Service.getEvents(runId, params);
      if (result.ok && result.data) {
        setEvents(result.data);
      } else {
        setError(result.error ?? 'Failed to load factory run events');
        setEvents(null);
      }
    } catch (e) {
      setError(String(e));
      setEvents(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchAuditBundle = useCallback(async (runId: string) => {
    setLoading(true);
    setError('');
    try {
      const result = await factoryRunV2Service.getAuditBundle(runId);
      if (result.ok && result.data) {
        setAuditBundle(result.data);
      } else {
        setError(result.error ?? 'Failed to load factory run audit bundle');
        setAuditBundle(null);
      }
    } catch (e) {
      setError(String(e));
      setAuditBundle(null);
    } finally {
      setLoading(false);
    }
  }, []);

  return { events, auditBundle, loading, error, fetchEvents, fetchAuditBundle };
}

// ============================================================================
// useLLMConfig
// ============================================================================

export interface UseLLMConfigResult {
  config: LLMConfigResponse | null;
  status: LLMStatusResponse | null;
  providers: LLMProviderListResponse | null;
  loading: boolean;
  error: string;
  refreshConfig: () => Promise<void>;
  refreshStatus: () => Promise<void>;
  refreshProviders: () => Promise<void>;
  migrate: (request: LLMConfigMigrateRequest) => Promise<LLMConfigMigrateResponse | null>;
}

export function useLLMConfig(): UseLLMConfigResult {
  const [config, setConfig] = useState<LLMConfigResponse | null>(null);
  const [status, setStatus] = useState<LLMStatusResponse | null>(null);
  const [providers, setProviders] = useState<LLMProviderListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refreshConfig = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await llmConfigService.get();
      if (result.ok && result.data) {
        setConfig(result.data);
      } else {
        setError(result.error ?? 'Failed to load LLM config');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await llmConfigService.getStatus();
      if (result.ok && result.data) {
        setStatus(result.data);
      } else {
        setError(result.error ?? 'Failed to load LLM status');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshProviders = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await llmConfigService.listProviders();
      if (result.ok && result.data) {
        setProviders(result.data);
      } else {
        setError(result.error ?? 'Failed to list LLM providers');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const migrate = useCallback(async (request: LLMConfigMigrateRequest): Promise<LLMConfigMigrateResponse | null> => {
    setLoading(true);
    setError('');
    try {
      const result = await llmConfigService.migrate(request);
      if (result.ok && result.data) {
        return result.data;
      }
      setError(result.error ?? 'Failed to migrate LLM config');
      return null;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    config,
    status,
    providers,
    loading,
    error,
    refreshConfig,
    refreshStatus,
    refreshProviders,
    migrate,
  };
}

// ============================================================================
// useSettings
// ============================================================================

export interface UseSettingsResult {
  settings: SettingsV2Response | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  update: (request: SettingsV2UpdateRequest) => Promise<SettingsV2Response | null>;
}

export function useSettings(): UseSettingsResult {
  const [settings, setSettings] = useState<SettingsV2Response | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await settingsV2Service.get();
      if (result.ok && result.data) {
        setSettings(result.data);
      } else {
        setError(result.error ?? 'Failed to load settings');
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const update = useCallback(async (request: SettingsV2UpdateRequest): Promise<SettingsV2Response | null> => {
    setLoading(true);
    setError('');
    try {
      const result = await settingsV2Service.update(request);
      if (result.ok && result.data) {
        setSettings(result.data);
        return result.data;
      }
      setError(result.error ?? 'Failed to update settings');
      return null;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { settings, loading, error, refresh, update };
}

// ============================================================================
// useHealth
// ============================================================================

export interface UseHealthResult {
  health: HealthV2Response | null;
  loading: boolean;
  error: string;
  check: () => Promise<void>;
}

export function useHealth(): UseHealthResult {
  const [health, setHealth] = useState<HealthV2Response | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const check = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const result = await healthV2Service.check();
      if (result.ok && result.data) {
        setHealth(result.data);
      } else {
        setError(result.error ?? 'Health check failed');
        setHealth(null);
      }
    } catch (e) {
      setError(String(e));
      setHealth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  return { health, loading, error, check };
}
