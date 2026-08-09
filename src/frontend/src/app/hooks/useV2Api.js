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
import { useState, useCallback, useEffect } from 'react';
import { roleChatService, conversationV2Service, factoryRunV2Service, llmConfigService, settingsV2Service, healthV2Service, } from '@/services/api';
function workspaceFromContext(context) {
    const workspace = context?.workspace;
    return typeof workspace === 'string' ? workspace.trim() : '';
}
export function useRoleChat(role, workspace = '') {
    const [response, setResponse] = useState('');
    const [thinking, setThinking] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const sendMessage = useCallback(async (message, context) => {
        setLoading(true);
        setError('');
        setResponse('');
        setThinking('');
        try {
            const request = { message, context };
            const result = await roleChatService.chat(role, request, workspace || workspaceFromContext(context));
            if (result.ok && result.data) {
                setResponse(result.data.response);
                setThinking(result.data.thinking ?? '');
            }
            else {
                setError(result.error ?? 'Role chat failed');
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setLoading(false);
        }
    }, [role, workspace]);
    const reset = useCallback(() => {
        setResponse('');
        setThinking('');
        setError('');
    }, []);
    return { response, thinking, loading, error, sendMessage, reset };
}
export function useRoleChatStatus(role, workspace = '') {
    const [status, setStatus] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const refresh = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const result = await roleChatService.getStatus(role, workspace);
            if (result.ok && result.data) {
                setStatus(result.data);
            }
            else {
                setError(result.error ?? 'Failed to load role chat status');
                setStatus(null);
            }
        }
        catch (e) {
            setError(String(e));
            setStatus(null);
        }
        finally {
            setLoading(false);
        }
    }, [role, workspace]);
    useEffect(() => {
        refresh();
    }, [refresh]);
    return { status, loading, error, refresh };
}
export function useConversations() {
    const [conversations, setConversations] = useState([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const list = useCallback(async (params) => {
        setLoading(true);
        setError('');
        try {
            const result = await conversationV2Service.list(params);
            if (result.ok && result.data) {
                setConversations(result.data.conversations ?? []);
                setTotal(result.data.total ?? 0);
            }
            else {
                setError(result.error ?? 'Failed to list conversations');
                setConversations([]);
                setTotal(0);
            }
        }
        catch (e) {
            setError(String(e));
            setConversations([]);
            setTotal(0);
        }
        finally {
            setLoading(false);
        }
    }, []);
    const create = useCallback(async (request) => {
        setLoading(true);
        setError('');
        try {
            const result = await conversationV2Service.create(request);
            if (result.ok && result.data) {
                setConversations((prev) => [result.data, ...prev]);
                setTotal((prev) => prev + 1);
                return result.data;
            }
            setError(result.error ?? 'Failed to create conversation');
            return null;
        }
        catch (e) {
            setError(String(e));
            return null;
        }
        finally {
            setLoading(false);
        }
    }, []);
    const getMessages = useCallback(async (conversationId, params) => {
        setLoading(true);
        setError('');
        try {
            const result = await conversationV2Service.getMessages(conversationId, params);
            if (result.ok && result.data) {
                return result.data;
            }
            setError(result.error ?? 'Failed to get conversation messages');
            return [];
        }
        catch (e) {
            setError(String(e));
            return [];
        }
        finally {
            setLoading(false);
        }
    }, []);
    const addMessage = useCallback(async (conversationId, request) => {
        setLoading(true);
        setError('');
        try {
            const result = await conversationV2Service.addMessage(conversationId, request);
            if (result.ok && result.data) {
                return result.data;
            }
            setError(result.error ?? 'Failed to add conversation message');
            return null;
        }
        catch (e) {
            setError(String(e));
            return null;
        }
        finally {
            setLoading(false);
        }
    }, []);
    return { conversations, total, loading, error, list, create, getMessages, addMessage };
}
export function useFactoryRuns() {
    const [events, setEvents] = useState(null);
    const [auditBundle, setAuditBundle] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const fetchEvents = useCallback(async (runId, params) => {
        setLoading(true);
        setError('');
        try {
            const result = await factoryRunV2Service.getEvents(runId, params);
            if (result.ok && result.data) {
                setEvents(result.data);
            }
            else {
                setError(result.error ?? 'Failed to load factory run events');
                setEvents(null);
            }
        }
        catch (e) {
            setError(String(e));
            setEvents(null);
        }
        finally {
            setLoading(false);
        }
    }, []);
    const fetchAuditBundle = useCallback(async (runId) => {
        setLoading(true);
        setError('');
        try {
            const result = await factoryRunV2Service.getAuditBundle(runId);
            if (result.ok && result.data) {
                setAuditBundle(result.data);
            }
            else {
                setError(result.error ?? 'Failed to load factory run audit bundle');
                setAuditBundle(null);
            }
        }
        catch (e) {
            setError(String(e));
            setAuditBundle(null);
        }
        finally {
            setLoading(false);
        }
    }, []);
    return { events, auditBundle, loading, error, fetchEvents, fetchAuditBundle };
}
export function useLLMConfig() {
    const [config, setConfig] = useState(null);
    const [status, setStatus] = useState(null);
    const [providers, setProviders] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const refreshConfig = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const result = await llmConfigService.get();
            if (result.ok && result.data) {
                setConfig(result.data);
            }
            else {
                setError(result.error ?? 'Failed to load LLM config');
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
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
            }
            else {
                setError(result.error ?? 'Failed to load LLM status');
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
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
            }
            else {
                setError(result.error ?? 'Failed to list LLM providers');
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setLoading(false);
        }
    }, []);
    const migrate = useCallback(async (request) => {
        setLoading(true);
        setError('');
        try {
            const result = await llmConfigService.migrate(request);
            if (result.ok && result.data) {
                return result.data;
            }
            setError(result.error ?? 'Failed to migrate LLM config');
            return null;
        }
        catch (e) {
            setError(String(e));
            return null;
        }
        finally {
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
export function useSettings() {
    const [settings, setSettings] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const refresh = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const result = await settingsV2Service.get();
            if (result.ok && result.data) {
                setSettings(result.data);
            }
            else {
                setError(result.error ?? 'Failed to load settings');
            }
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setLoading(false);
        }
    }, []);
    const update = useCallback(async (request) => {
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
        }
        catch (e) {
            setError(String(e));
            return null;
        }
        finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => {
        refresh();
    }, [refresh]);
    return { settings, loading, error, refresh, update };
}
export function useHealth() {
    const [health, setHealth] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const check = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const result = await healthV2Service.check();
            if (result.ok && result.data) {
                setHealth(result.data);
            }
            else {
                setError(result.error ?? 'Health check failed');
                setHealth(null);
            }
        }
        catch (e) {
            setError(String(e));
            setHealth(null);
        }
        finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => {
        check();
    }, [check]);
    return { health, loading, error, check };
}
