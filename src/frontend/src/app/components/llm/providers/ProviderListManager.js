import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * ProviderListManager Component
 * Provider 列表管理组件
 */
import { useCallback, useMemo } from 'react';
import { Plus, Settings, PlayCircle } from 'lucide-react';
import { useConnectivityStore, useProviderActions, useProviderState, useSelectedMethod, } from '../state';
import { ProviderCard } from './ProviderCard';
import { ConnectionMethodSelector } from './ConnectionMethodSelector';
const PROVIDER_FAMILY_ORDER = [
    'Codex',
    'OpenAI',
    'Anthropic',
    'Gemini',
    'MiniMax',
    'Ollama',
    'Custom',
    'Other',
];
function resolveProviderFamily(providerType, providerName) {
    const type = providerType.toLowerCase();
    const name = providerName.toLowerCase();
    if (type.includes('codex') || name.includes('codex'))
        return 'Codex';
    if (type.includes('openai') || name.includes('openai'))
        return 'OpenAI';
    if (type.includes('anthropic') || name.includes('anthropic'))
        return 'Anthropic';
    if (type.includes('gemini') || name.includes('gemini'))
        return 'Gemini';
    if (type.includes('minimax') || name.includes('minimax'))
        return 'MiniMax';
    if (type.includes('ollama') || name.includes('ollama'))
        return 'Ollama';
    if (type.includes('custom'))
        return 'Custom';
    return 'Other';
}
function resolveConnectionMethod(providerType) {
    const normalized = String(providerType || '').toLowerCase();
    if (normalized.includes('sdk'))
        return 'sdk';
    if (normalized.includes('cli'))
        return 'cli';
    return 'api';
}
export function ProviderListManager({ providers, configuredProviders, llmStatus, isSaving, deletingProviders, getProviderInfo, getProviderComponent, getCostClass, onAddProvider, onUpdateProvider, onDeleteProvider, onTestProvider, onEnterDeepTest, }) {
    const { state } = useProviderState();
    const selectedMethod = useSelectedMethod();
    const { openTestPanel } = useProviderActions();
    const providerTestStatusMap = state.providerTestStatus;
    const { getLatestProviderConnectivity } = useConnectivityStore();
    const availableMethods = useMemo(() => {
        const methodSet = new Set();
        providers.forEach((provider) => {
            methodSet.add(resolveConnectionMethod(provider.info.type));
        });
        return Array.from(methodSet);
    }, [providers]);
    const filteredProviderEntries = useMemo(() => {
        return providers.filter((provider) => {
            return resolveConnectionMethod(provider.info.type) === selectedMethod;
        });
    }, [providers, selectedMethod]);
    const providerGroups = useMemo(() => {
        const groups = new Map();
        filteredProviderEntries.forEach((provider) => {
            const family = resolveProviderFamily(provider.info.type, provider.info.name);
            const existing = groups.get(family) || [];
            groups.set(family, [...existing, provider]);
        });
        const ordered = [];
        PROVIDER_FAMILY_ORDER.forEach((family) => {
            const entries = groups.get(family);
            if (entries && entries.length > 0) {
                ordered.push([family, entries]);
            }
        });
        groups.forEach((entries, family) => {
            if (!PROVIDER_FAMILY_ORDER.includes(family)) {
                ordered.push([family, entries]);
            }
        });
        return ordered;
    }, [filteredProviderEntries]);
    const configuredProviderCount = Object.keys(configuredProviders).length;
    const hasConfiguredProviders = configuredProviderCount > 0;
    const handleAddProvider = useCallback((providerType) => {
        const providerEntry = getProviderInfo(providerType);
        if (!providerEntry)
            return;
        const providerId = `${providerType}-${Date.now()}`;
        const newProvider = {
            ...providerEntry.defaultConfig,
            name: providerEntry.defaultConfig.name || `${providerType} 提供商`,
            type: providerType,
        };
        onAddProvider(providerId, newProvider);
    }, [getProviderInfo, onAddProvider]);
    const recommendedProvider = useMemo(() => {
        const primaryType = selectedMethod === 'sdk'
            ? 'codex_sdk'
            : selectedMethod === 'cli'
                ? 'codex_cli'
                : 'openai_compat';
        const preferred = providers.find((p) => p.info.type === primaryType);
        if (preferred)
            return preferred;
        return providers.find((p) => resolveConnectionMethod(p.info.type) === selectedMethod) || null;
    }, [providers, selectedMethod]);
    return (_jsxs("div", { className: "space-y-4", children: [_jsx(ConnectionMethodSelector, { availableMethods: availableMethods }), _jsxs("div", { className: "soft-panel-subtle rounded-xl p-4", children: [_jsxs("div", { className: "mb-3 flex min-w-0 flex-col gap-2 lg:flex-row lg:items-start lg:justify-between", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "text-xs font-semibold text-text-main", children: "\u652F\u6301\u7684\u63D0\u4F9B\u5546" }), _jsxs("div", { className: "text-[10px] text-text-dim", children: ["\u5F53\u524D\u663E\u793A\uFF1A", selectedMethod === 'sdk' ? 'SDK 方式' : selectedMethod === 'cli' ? '命令行方式' : 'HTTP API 方式'] })] }), _jsx("div", { className: "break-words text-[10px] text-text-dim", children: "\u9009\u62E9\u540E\u5C06\u81EA\u52A8\u521B\u5EFA\u914D\u7F6E\u5E76\u8FDB\u5165\u7F16\u8F91\u6A21\u5F0F\u3002" })] }), providerGroups.length === 0 ? (_jsx("div", { className: "text-xs text-text-dim", children: "\u6682\u65E0\u53EF\u7528\u63D0\u4F9B\u5546" })) : (_jsx("div", { className: "space-y-4", children: providerGroups.map(([family, entries]) => (_jsxs("div", { className: "space-y-2", children: [_jsx("div", { className: "text-[11px] uppercase tracking-wider text-text-dim", children: family }), _jsx("div", { className: "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3", children: entries.map((provider) => (_jsxs("button", { type: "button", onClick: () => handleAddProvider(provider.info.type), disabled: isSaving, className: "soft-panel-subtle min-w-0 rounded-xl p-3 text-left transition-all hover:border-accent/35 hover:shadow-[0_16px_34px_rgba(31,43,51,0.11)] disabled:opacity-60", children: [_jsxs("div", { className: "flex min-w-0 items-start justify-between gap-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-xs font-semibold text-text-main", children: provider.info.name }), _jsx("div", { className: "break-all text-[10px] text-text-dim", children: provider.info.type })] }), _jsx("span", { className: "soft-chip shrink-0 px-2 py-0.5 text-[9px] text-text-muted", children: provider.info.cost_class })] }), _jsx("div", { className: "mt-2 line-clamp-2 break-words text-[10px] text-text-dim", children: provider.info.description }), provider.info.supported_features?.length ? (_jsx("div", { className: "mt-2 flex flex-wrap gap-1", children: provider.info.supported_features.slice(0, 3).map((feature) => (_jsx("span", { className: "soft-chip px-2 py-0.5 text-[9px] text-text-dim", children: feature }, feature))) })) : null, _jsxs("div", { className: "mt-2 flex items-center justify-between text-[10px] text-text-dim", children: [_jsx("span", { children: "\u70B9\u51FB\u6DFB\u52A0\u5E76\u914D\u7F6E" }), _jsx(Plus, { className: "size-3" })] })] }, provider.info.type))) })] }, family))) })), recommendedProvider && (_jsxs("div", { className: "mt-4 flex min-w-0 flex-wrap items-center justify-between gap-2 rounded-xl border border-status-success/35 bg-status-success/10 p-3 text-[10px] text-status-success", children: [_jsxs("div", { className: "min-w-0 break-words", children: ["\u63A8\u8350\u63D0\u4F9B\u5546\uFF1A", _jsx("span", { className: "font-semibold", children: recommendedProvider.info.name }), _jsxs("span", { className: "text-text-muted", children: [" \u00B7 ", recommendedProvider.info.description] })] }), _jsx("button", { type: "button", onClick: () => handleAddProvider(recommendedProvider.info.type), className: "soft-primary-action rounded px-3 py-1.5 text-[10px] font-semibold transition-colors", children: "\u4E00\u952E\u6DFB\u52A0" })] }))] }), hasConfiguredProviders ? (_jsxs("div", { className: "space-y-3", children: [Object.entries(configuredProviders).map(([providerId, provider]) => {
                        const providerEntry = getProviderInfo(provider.type || '');
                        const ProviderComponent = getProviderComponent(provider.type || '');
                        const cachedConnectivityStatus = providerTestStatusMap[providerId] || 'unknown';
                        const latestConnectivity = getLatestProviderConnectivity(providerId);
                        const persistedConnectivitySuiteRaw = llmStatus?.providers?.[providerId]?.suites?.connectivity;
                        const persistedConnectivitySuite = persistedConnectivitySuiteRaw && typeof persistedConnectivitySuiteRaw === 'object'
                            ? persistedConnectivitySuiteRaw
                            : undefined;
                        const persistedConnectivityOk = typeof persistedConnectivitySuite?.ok === 'boolean'
                            ? persistedConnectivitySuite.ok
                            : undefined;
                        const connectivityStatus = cachedConnectivityStatus !== 'unknown'
                            ? cachedConnectivityStatus
                            : latestConnectivity
                                ? latestConnectivity.ok
                                    ? 'success'
                                    : 'failed'
                                : persistedConnectivityOk === true
                                    ? 'success'
                                    : persistedConnectivityOk === false
                                        ? 'failed'
                                        : 'unknown';
                        return (_jsx(ProviderCard, { providerId: providerId, provider: provider, providerInfo: providerEntry?.info || null, ProviderComponent: ProviderComponent, connectivityStatus: connectivityStatus, costClass: getCostClass(provider.type || ''), isDeleting: deletingProviders?.[providerId], isSaving: isSaving, llmStatus: llmStatus, onUpdate: onUpdateProvider, onDelete: onDeleteProvider, onTest: (id) => {
                                openTestPanel(id);
                                onTestProvider(id);
                            } }, providerId));
                    }), _jsxs("div", { className: "flex flex-col items-center gap-2", children: [_jsxs("span", { className: "text-[10px] text-text-dim", children: ["\u914D\u7F6E\u72B6\u6001\uFF1A", configuredProviderCount, " \u4E2A\u63D0\u4F9B\u5546\u5DF2\u51C6\u5907"] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("button", { type: "button", onClick: onEnterDeepTest, className: "soft-primary-action flex items-center gap-2 rounded px-4 py-2 text-xs font-semibold transition-colors", children: ["\u8FDB\u5165\u6DF1\u5EA6\u6D4B\u8BD5", _jsx(PlayCircle, { className: "size-3" })] }), _jsx("button", { type: "button", onClick: () => {
                                            // 直接打开第一个配置提供商的测试面板，跳过连通性测试
                                            const firstProviderId = Object.keys(configuredProviders)[0];
                                            if (firstProviderId) {
                                                openTestPanel(firstProviderId);
                                            }
                                        }, className: "rounded border border-status-warning/35 px-3 py-2 text-[10px] text-status-warning transition-colors hover:border-status-warning/60 hover:bg-status-warning/10", title: "\u8DF3\u8FC7\u8FDE\u901A\u6027\u6D4B\u8BD5\uFF0C\u76F4\u63A5\u6253\u5F00\u6D4B\u8BD5\u9762\u677F", children: "\u76F4\u63A5\u6253\u5F00\u9762\u677F" })] })] })] })) : (_jsxs("div", { className: "soft-panel-subtle space-y-4 rounded-xl p-8 text-center", children: [_jsx(Settings, { className: "size-8 text-text-dim mx-auto mb-3" }), _jsx("h4", { className: "text-sm font-medium text-text-main mb-2", children: "\u5C1A\u672A\u914D\u7F6ELLM\u63D0\u4F9B\u5546" }), _jsx("p", { className: "text-xs text-text-dim mb-4", children: "\u9009\u62E9\u4E00\u4E2A\u63D0\u4F9B\u5546\u7C7B\u578B\u5E76\u6DFB\u52A0\u914D\u7F6E\uFF0C\u7136\u540E\u8FDB\u884C\u6A21\u578B\u6D4B\u8BD5" }), _jsxs("div", { className: "text-xs text-text-dim", children: [_jsx("p", { children: "\u652F\u6301\u7684\u63D0\u4F9B\u5546\u7C7B\u578B\uFF1A" }), _jsx("div", { className: "flex flex-wrap gap-2 justify-center mt-2", children: providers.slice(0, 6).map((provider) => (_jsx("span", { className: "soft-chip rounded px-2 py-1 text-[9px]", children: provider.info.name }, provider.info.type))) })] }), _jsxs("div", { className: "flex flex-col items-center gap-2", children: [_jsxs("span", { className: "text-[10px] text-text-dim", children: ["\u914D\u7F6E\u72B6\u6001\uFF1A", configuredProviderCount, " \u4E2A\u63D0\u4F9B\u5546"] }), _jsxs("button", { type: "button", onClick: onEnterDeepTest, className: "soft-primary-action flex items-center gap-2 rounded px-4 py-2 text-xs font-semibold opacity-80 transition-colors hover:opacity-100", children: ["\u8FDB\u5165\u6DF1\u5EA6\u6D4B\u8BD5\uFF08\u65E0\u914D\u7F6E\uFF09", _jsx(PlayCircle, { className: "size-3" })] })] })] }))] }));
}
