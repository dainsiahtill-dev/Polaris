import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * LLMSettingsBridge
 *
 * Bridge component that connects the LLM settings tab to the settings modal.
 * This is a thin wrapper around the LLMSettingsTab component that provides
 * the necessary props and callbacks.
 */
import { lazy, Suspense, useState, useCallback, useEffect, useRef } from 'react';
import { Loader2 } from 'lucide-react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import { isCLIProviderType } from '@/app/components/llm/types';
import { sanitizeLlmConfigForSave } from '@/app/components/llm/utils/configSanitizer';
// Lazy load the heavy LLMSettingsTab component
const LLMSettingsTab = lazy(() => import('@/app/components/llm/LLMSettingsTab').then((module) => ({ default: module.LLMSettingsTab })));
/**
 * Build test result from API response
 */
function buildTestResult(report) {
    let final = report?.final;
    if (!final && typeof report?.ready === 'boolean') {
        final = report;
    }
    const runId = typeof report?.test_run_id === 'string' ? report.test_run_id : undefined;
    const ready = typeof final?.ready === 'boolean' ? final.ready : undefined;
    const grade = typeof final?.grade === 'string' ? final.grade : undefined;
    return {
        report,
        runId,
        ready,
        grade,
    };
}
/**
 * LLM Settings Bridge Component
 */
export function LLMSettingsBridge({ onLlmStatusChange }) {
    const [llmConfig, setLLMConfig] = useState(null);
    const [llmStatus, setLLMStatus] = useState(null);
    const [llmLoading, setLlmLoading] = useState(false);
    const [llmSaving, setLlmSaving] = useState(false);
    const [llmError, setLlmError] = useState(null);
    const [deletingProviders, setDeletingProviders] = useState({});
    const llmConfigRef = useRef(null);
    const llmSavePendingRef = useRef(null);
    const llmSaveQueueRef = useRef(Promise.resolve(true));
    // Load LLM config
    const loadLLMConfig = useCallback(async () => {
        setLlmLoading(true);
        setLlmError(null);
        try {
            const res = await apiFetch('/v2/llm/config');
            if (!res.ok) {
                throw new Error('读取 LLM 配置失败');
            }
            const data = (await res.json());
            setLLMConfig(data);
            llmConfigRef.current = data;
        }
        catch (err) {
            setLlmError(err instanceof Error ? err.message : '读取 LLM 配置失败');
        }
        finally {
            setLlmLoading(false);
        }
    }, []);
    // Load LLM status
    const loadLLMStatus = useCallback(async () => {
        try {
            const res = await apiFetch('/v2/llm/status');
            if (!res.ok) {
                throw new Error('读取 LLM 状态失败');
            }
            const data = (await res.json());
            setLLMStatus(data);
            onLlmStatusChange?.(data);
        }
        catch {
            setLLMStatus(null);
            onLlmStatusChange?.(null);
        }
    }, [onLlmStatusChange]);
    // Initial load
    useEffect(() => {
        loadLLMConfig();
        loadLLMStatus();
    }, [loadLLMConfig, loadLLMStatus]);
    // Queue LLM save
    const queueLlmSave = useCallback(async (nextConfig) => {
        llmSavePendingRef.current = nextConfig;
        const run = async () => {
            if (!llmSavePendingRef.current)
                return true;
            setLlmSaving(true);
            setLlmError(null);
            let success = true;
            while (llmSavePendingRef.current) {
                const configToSave = sanitizeLlmConfigForSave(llmSavePendingRef.current);
                llmSavePendingRef.current = null;
                setLLMConfig(configToSave);
                llmConfigRef.current = configToSave;
                try {
                    const res = await apiFetch('/v2/llm/config', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(configToSave),
                    });
                    if (!res.ok) {
                        throw new Error('保存 LLM 配置失败');
                    }
                    const data = (await res.json());
                    setLLMConfig(data);
                    llmConfigRef.current = data;
                    await loadLLMStatus();
                }
                catch (err) {
                    setLlmError(err instanceof Error ? err.message : '保存 LLM 配置失败');
                    success = false;
                    llmSavePendingRef.current = null;
                    break;
                }
            }
            setLlmSaving(false);
            return success;
        };
        const runPromise = llmSaveQueueRef.current.then(run, run);
        llmSaveQueueRef.current = runPromise;
        return runPromise;
    }, [loadLLMStatus]);
    // Apply config mutation
    const applyLLMConfigMutation = useCallback(async (mutator) => {
        const current = llmConfigRef.current;
        if (!current)
            return null;
        const nextConfig = mutator(current);
        setLLMConfig(nextConfig);
        llmConfigRef.current = nextConfig;
        return nextConfig;
    }, []);
    // Save config handler
    const handleSaveConfig = useCallback(async (config) => {
        const target = config || llmConfigRef.current;
        if (!target)
            return true;
        return queueLlmSave(target);
    }, [queueLlmSave]);
    // Add provider
    const handleAddProvider = useCallback(async (providerId, provider) => {
        await applyLLMConfigMutation((current) => ({
            ...current,
            providers: {
                ...(current.providers || {}),
                [providerId]: provider,
            },
        }));
    }, [applyLLMConfigMutation]);
    // Update provider
    const handleUpdateProvider = useCallback(async (providerId, updates) => {
        await applyLLMConfigMutation((current) => ({
            ...current,
            providers: {
                ...(current.providers || {}),
                [providerId]: {
                    ...(current.providers?.[providerId] || {}),
                    ...updates,
                },
            },
        }));
    }, [applyLLMConfigMutation]);
    // Delete provider
    const handleDeleteProvider = useCallback(async (providerId) => {
        setDeletingProviders((prev) => ({ ...prev, [providerId]: true }));
        try {
            await applyLLMConfigMutation((current) => {
                const nextProviders = { ...(current.providers || {}) };
                delete nextProviders[providerId];
                const nextRoles = { ...(current.roles || {}) };
                Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
                    if (roleCfg?.provider_id === providerId) {
                        const nextRoleCfg = { ...(roleCfg || {}) };
                        delete nextRoleCfg.provider_id;
                        delete nextRoleCfg.model;
                        nextRoles[roleId] = nextRoleCfg;
                    }
                });
                return { ...current, providers: nextProviders, roles: nextRoles };
            });
        }
        finally {
            setDeletingProviders((prev) => {
                const next = { ...prev };
                delete next[providerId];
                return next;
            });
        }
    }, [applyLLMConfigMutation]);
    // Update config
    const handleUpdateConfig = useCallback((config) => {
        setLLMConfig(config);
        llmConfigRef.current = config;
    }, []);
    // Test provider
    const handleTestProvider = useCallback(async (provider, onEvent) => {
        try {
            const res = await apiFetch('/v2/llm/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    provider_id: provider.id,
                    provider_type: provider.kind,
                    model: provider.modelId,
                }),
            });
            if (!res.ok) {
                throw new Error('测试请求失败');
            }
            const report = (await res.json());
            return buildTestResult(report);
        }
        catch (err) {
            devLogger.error('Provider test failed:', err);
            return null;
        }
    }, []);
    // Run interview
    const handleRunInterview = useCallback(async (role, providerId, model, onEvent) => {
        try {
            const res = await apiFetch('/llm/interview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, provider_id: providerId, model }),
            });
            if (!res.ok) {
                throw new Error('面试请求失败');
            }
            return (await res.json());
        }
        catch (err) {
            devLogger.error('Interview failed:', err);
            return null;
        }
    }, []);
    // Run connectivity test
    const handleRunConnectivityTest = useCallback(async (role, providerId, model) => {
        try {
            const res = await apiFetch('/llm/connectivity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ role, provider_id: providerId, model }),
            });
            if (!res.ok) {
                throw new Error('连通性测试失败');
            }
            return (await res.json());
        }
        catch (err) {
            devLogger.error('Connectivity test failed:', err);
            return null;
        }
    }, []);
    // Ask interactive interview
    const handleAskInteractiveInterview = useCallback(async (payload) => {
        try {
            const res = await apiFetch('/v2/llm/interview/ask', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                throw new Error('交互面试请求失败');
            }
            return (await res.json());
        }
        catch (err) {
            devLogger.error('Interactive interview failed:', err);
            return null;
        }
    }, []);
    // Save interactive interview
    const handleSaveInteractiveInterview = useCallback(async (payload) => {
        try {
            const res = await apiFetch('/v2/llm/interview/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                throw new Error('保存面试报告失败');
            }
            const data = (await res.json());
            if (data.saved) {
                await loadLLMStatus();
            }
            return data;
        }
        catch (err) {
            devLogger.error('Save interview failed:', err);
            return null;
        }
    }, [loadLLMStatus]);
    // Resolve provider env overrides
    const handleResolveEnvOverrides = useCallback(async (providerId) => {
        const cfg = llmConfigRef.current?.providers?.[providerId];
        if (!cfg || !isCLIProviderType(String(cfg.type || ''))) {
            return null;
        }
        const env = cfg.env && typeof cfg.env === 'object' ? cfg.env : {};
        const resolved = {};
        for (const [key, value] of Object.entries(env)) {
            if (value === undefined || value === null) {
                continue;
            }
            const raw = String(value).trim();
            const match = raw.match(/^\$?\{?keychain:([^}]+)\}?$/i);
            if (match && window.polaris?.secrets?.get) {
                try {
                    const result = await window.polaris.secrets.get(match[1]);
                    if (result?.ok && result.value) {
                        resolved[key] = String(result.value);
                    }
                }
                catch {
                    // Keep env resolution best-effort; the test request still carries non-secret overrides.
                }
            }
            else {
                resolved[key] = raw;
            }
        }
        return Object.keys(resolved).length > 0 ? resolved : null;
    }, []);
    return (_jsx(Suspense, { fallback: _jsx("div", { className: "flex items-center justify-center py-12", children: _jsxs("div", { className: "flex items-center gap-2 text-text-muted", children: [_jsx(Loader2, { className: "size-4 animate-spin" }), _jsx("span", { className: "text-sm", children: "\u6B63\u5728\u8F7D\u5165 LLM \u914D\u7F6E..." })] }) }), children: _jsx(LLMSettingsTab, { llmConfig: llmConfig, llmStatus: llmStatus, llmLoading: llmLoading, llmSaving: llmSaving, llmError: llmError, deletingProviders: deletingProviders, onSaveConfig: handleSaveConfig, onRunInterview: handleRunInterview, onRunConnectivityTest: handleRunConnectivityTest, onAskInteractiveInterview: handleAskInteractiveInterview, onSaveInteractiveInterview: handleSaveInteractiveInterview, resolveProviderEnvOverrides: handleResolveEnvOverrides, onAddProvider: handleAddProvider, onUpdateProvider: handleUpdateProvider, onDeleteProvider: handleDeleteProvider, onUpdateConfig: handleUpdateConfig, onTestProvider: handleTestProvider }) }));
}
