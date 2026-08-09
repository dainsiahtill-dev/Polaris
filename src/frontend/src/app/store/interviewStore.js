/**
 * Interview Store - Zustand 状态管理
 * 集中管理面试和 TUI 抽屉相关状态
 */
import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { apiFetch } from '@/api';
import { isCLIProviderType } from '@/app/components/llm/types';
// ============================================================================
// Helper Functions
// ============================================================================
const resolveApiKey = async (providerId, cfg) => {
    if (cfg.type !== 'openai_compat' && cfg.type !== 'anthropic_compat' && cfg.type !== 'codex_sdk')
        return null;
    if (!window.polaris?.secrets?.get)
        return null;
    const keyRef = cfg.api_key_ref || `keychain:llm:${providerId}`;
    const keyName = keyRef.startsWith('keychain:') ? keyRef.slice('keychain:'.length) : keyRef;
    try {
        const result = await window.polaris.secrets.get(keyName);
        if (result?.ok && result.value)
            return String(result.value);
    }
    catch { /* ignore */ }
    return null;
};
const resolveEnvOverrides = async (providerId, cfg) => {
    if (!isCLIProviderType(String(cfg.type || '')))
        return null;
    const env = cfg.env && typeof cfg.env === 'object' ? cfg.env : {};
    const resolved = {};
    for (const [key, value] of Object.entries(env)) {
        if (value === undefined || value === null)
            continue;
        const raw = String(value).trim();
        const match = raw.match(/^\$?\{?keychain:([^}]+)\}?$/i);
        if (match && window.polaris?.secrets?.get) {
            try {
                const result = await window.polaris.secrets.get(match[1]);
                if (result?.ok && result.value)
                    resolved[key] = String(result.value);
            }
            catch { /* ignore */ }
        }
        else {
            resolved[key] = raw;
        }
    }
    return { env: resolved };
};
const resolveModel = (providerCfg) => {
    if (!providerCfg)
        return '';
    return typeof providerCfg.model === 'string' ? providerCfg.model :
        typeof providerCfg.model_id === 'string' ? providerCfg.model_id :
            typeof providerCfg.default_model === 'string' ? providerCfg.default_model : '';
};
// ============================================================================
// Store Creation
// ============================================================================
export const useInterviewStore = create()(immer((set) => {
    let interviewAbortRef = null;
    return {
        // ============ 初始状态 ============
        tuiDrawer: { open: false, role: '', providerId: '' },
        shouldMountTuiDrawer: false,
        tuiModelDraft: '',
        tuiError: null,
        llmError: null,
        // ============ TUI抽屉操作 ============
        setTuiDrawer: (drawer) => set((state) => { state.tuiDrawer = { ...state.tuiDrawer, ...drawer }; }),
        setShouldMountTuiDrawer: (mount) => set((state) => { state.shouldMountTuiDrawer = mount; }),
        setTuiModelDraft: (draft) => set((state) => { state.tuiModelDraft = draft; }),
        setTuiError: (error) => set((state) => { state.tuiError = error; }),
        openTuiDrawer: (role, providerId, currentModel) => set((state) => {
            state.tuiDrawer = { open: true, role, providerId };
            state.tuiModelDraft = currentModel;
            state.tuiError = null;
            state.shouldMountTuiDrawer = true;
        }),
        closeTuiDrawer: () => set((state) => {
            state.tuiDrawer.open = false;
            state.tuiError = null;
        }),
        // ============ 面试执行 ============
        runInterview: async (role, llmConfig, providerIdOverride, modelOverride, onEvent) => {
            if (!llmConfig)
                return null;
            const roleCfg = llmConfig.roles?.[role];
            const providerId = providerIdOverride || roleCfg?.provider_id;
            if (!providerId) {
                onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: '缺少角色提供商' });
                return null;
            }
            const providerCfg = llmConfig.providers?.[providerId];
            if (!providerCfg) {
                onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: '提供商未配置' });
                return null;
            }
            const providerModel = resolveModel(providerCfg);
            const roleModel = roleCfg?.model;
            let model = modelOverride || providerModel || roleModel;
            if (!model) {
                onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: '缺少模型' });
                return null;
            }
            if (interviewAbortRef)
                interviewAbortRef.abort();
            const controller = new AbortController();
            interviewAbortRef = controller;
            try {
                const res = await apiFetch('/v2/llm/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        role,
                        provider_id: providerId,
                        model,
                        suites: ['thinking', 'interview'],
                        test_level: 'full',
                        api_key: await resolveApiKey(providerId, providerCfg),
                        env_overrides: (await resolveEnvOverrides(providerId, providerCfg))?.env,
                        signal: controller.signal,
                    }),
                });
                if (!res.ok) {
                    const detail = await res.text().catch(() => res.statusText);
                    onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: `面试失败: ${detail}` });
                    return null;
                }
                const report = await res.json();
                const final = report.final;
                onEvent?.({ type: typeof final?.ready === 'boolean' && final.ready ? 'result' : 'error', timestamp: new Date().toISOString(), content: typeof final?.ready === 'boolean' && final.ready ? '面试完成' : '面试未通过' });
                return report;
            }
            catch (err) {
                if (err instanceof DOMException && err.name === 'AbortError')
                    return null;
                const message = err instanceof Error ? err.message : '面试请求失败';
                onEvent?.({ type: 'error', timestamp: new Date().toISOString(), content: message });
                set((s) => { s.llmError = message; });
                return null;
            }
            finally {
                if (interviewAbortRef === controller)
                    interviewAbortRef = null;
            }
        },
        askInteractiveInterview: async (payload) => {
            if (!payload.llmConfig)
                return null;
            const providerCfg = payload.llmConfig.providers?.[payload.providerId];
            if (!providerCfg)
                throw new Error('提供商未配置');
            const res = await apiFetch('/v2/llm/interview/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    role: payload.roleId,
                    provider_id: payload.providerId,
                    model: payload.model,
                    question: payload.question,
                    context: payload.context,
                    expects_thinking: payload.expectsThinking,
                    criteria: payload.expectedCriteria,
                    session_id: payload.sessionId,
                    api_key: await resolveApiKey(payload.providerId, providerCfg),
                    env_overrides: (await resolveEnvOverrides(payload.providerId, providerCfg))?.env,
                }),
            });
            if (!res.ok)
                throw new Error(await res.text().catch(() => '发送问题失败'));
            const data = await res.json();
            return {
                sessionId: String(data.session_id || data.sessionId || payload.sessionId || ''),
                answer: String(data.answer || data.output || ''),
                output: typeof data.output === 'string' ? data.output : undefined,
                thinking: typeof data.thinking === 'string' ? data.thinking : undefined,
                latencyMs: typeof data.latency_ms === 'number' ? data.latency_ms : undefined,
                ok: typeof data.ok === 'boolean' ? data.ok : undefined,
                error: typeof data.error === 'string' ? data.error : null,
                debug: typeof data.debug === 'object' ? data.debug : undefined,
            };
        },
        saveInteractiveInterview: async (payload) => {
            if (!payload.model)
                throw new Error('Model is required');
            const res = await apiFetch('/v2/llm/interview/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role: payload.roleId, provider_id: payload.providerId, model: payload.model, report: payload.report }),
            });
            if (!res.ok)
                throw new Error(await res.text().catch(() => '保存失败'));
            return await res.json();
        },
        setLlmError: (error) => set((state) => { state.llmError = error; }),
        cancelInterview: () => { interviewAbortRef?.abort(); interviewAbortRef = null; },
    };
}));
// ============================================================================
// Selector Hooks
// ============================================================================
export const useTuiDrawerState = () => useInterviewStore((state) => ({
    tuiDrawer: state.tuiDrawer,
    shouldMountTuiDrawer: state.shouldMountTuiDrawer,
    tuiModelDraft: state.tuiModelDraft,
    tuiError: state.tuiError,
}));
