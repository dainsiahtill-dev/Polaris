import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { devLogger } from '@/app/utils/devLogger';
import { RefreshCw, Check, AlertCircle } from 'lucide-react';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';
const cyberSelectClasses = "flex h-9 w-full min-w-0 rounded-md border border-border bg-[rgba(6,15,28,0.88)] px-3 py-1 text-sm text-text-main shadow-[inset_0_1px_0_rgba(178,245,255,0.14),inset_0_-1px_0_rgba(0,0,0,0.38)] transition-all duration-200 outline-none hover:border-accent/45 hover:bg-[rgba(10,25,44,0.92)] focus:border-accent/70 focus:bg-[rgba(10,25,44,0.96)] focus:ring-2 focus:ring-accent/25 cursor-pointer appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2300d8ff%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10";
export function OllamaProviderSettings({ provider, onUpdate, onValidate }) {
    const [availableModels, setAvailableModels] = useState([]);
    const [isLoadingModels, setIsLoadingModels] = useState(false);
    const [modelError, setModelError] = useState(null);
    const [isCustomModel, setIsCustomModel] = useState(false);
    const handleFieldChange = (field, value) => {
        onUpdate({ [field]: value });
    };
    const fetchModels = async () => {
        setIsLoadingModels(true);
        setModelError(null);
        const baseUrl = provider.base_url || 'http://127.0.0.1:11434';
        try {
            // Clean up base URL to ensure valid fetch
            const url = new URL('/api/tags', baseUrl).toString();
            const response = await fetch(url);
            if (!response.ok) {
                throw new Error(`连接失败：${response.statusText}`);
            }
            const data = await response.json();
            const models = (data.models || []).map((m) => m.name);
            setAvailableModels(models);
            // If current model is not in list and not empty, set as custom
            if (provider.model && !models.includes(provider.model)) {
                setIsCustomModel(true);
            }
        }
        catch (error) {
            devLogger.error('Failed to fetch Ollama models:', error);
            setModelError(error instanceof Error ? error.message : '获取模型列表失败');
        }
        finally {
            setIsLoadingModels(false);
        }
    };
    // Initial fetch if URL is present
    useEffect(() => {
        if (provider.base_url) {
            fetchModels();
        }
    }, []); // Only modify this if we want auto-refetch on URL change, but manual is safer for edits
    const handleModelSelect = (e) => {
        const value = e.target.value;
        if (value === 'custom') {
            setIsCustomModel(true);
            // Don't clear model immediately to allow "editing" current if valid, 
            // or clear if starting fresh. For now keep current.
        }
        else {
            setIsCustomModel(false);
            handleFieldChange('model', value);
        }
    };
    return (_jsx(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, children: _jsxs("div", { className: "space-y-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "Ollama \u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "Ollama \u670D\u52A1 URL" }), _jsx("div", { className: "flex gap-2", children: _jsx("input", { type: "text", "data-testid": "ollama-base-url-input", value: provider.base_url || 'http://127.0.0.1:11434', onChange: (e) => handleFieldChange('base_url', e.target.value), className: `${cyberInputClasses} flex-1 font-mono`, placeholder: "http://127.0.0.1:11434" }) }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u672C\u5730 Ollama \u670D\u52A1\u5730\u5740\uFF08\u9ED8\u8BA4\uFF1Ahttp://127.0.0.1:11434\uFF09" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u8DEF\u5F84" }), _jsxs("select", { "data-testid": "ollama-api-path-select", value: provider.api_path || '/api/chat', onChange: (e) => handleFieldChange('api_path', e.target.value), className: cyberSelectClasses, children: [_jsx("option", { value: "/api/chat", children: "\u804A\u5929\u63A5\u53E3\uFF08/api/chat\uFF09" }), _jsx("option", { value: "/api/generate", children: "\u751F\u6210\u63A5\u53E3\uFF08/api/generate\uFF09" }), _jsx("option", { value: "/v1/chat/completions", children: "OpenAI \u517C\u5BB9\uFF08/v1/chat/completions\uFF09" })] })] }), (provider.api_path || '').startsWith('/v1/') && (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API Key" }), _jsx("input", { type: "password", "data-testid": "ollama-api-key-input", value: provider.api_key || 'ollama', onChange: (e) => handleFieldChange('api_key', e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "ollama" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "OpenAI \u517C\u5BB9\u6A21\u5F0F\u9700\u8981 API Key\uFF08\u53EF\u4F7F\u7528\u5360\u4F4D\u7B26 \"ollama\"\uFF09" })] })), _jsxs("div", { className: "soft-panel-subtle rounded-lg p-3", children: [_jsxs("div", { className: "flex items-center justify-between mb-2", children: [_jsx("label", { className: "text-xs font-semibold text-text-main", children: "\u6A21\u578B\u9009\u62E9" }), _jsxs("button", { type: "button", onClick: fetchModels, disabled: isLoadingModels, className: "flex items-center gap-1 text-[10px] text-accent-text hover:text-accent disabled:opacity-50", children: [_jsx(RefreshCw, { className: `size-3 ${isLoadingModels ? 'animate-spin' : ''}` }), isLoadingModels ? '扫描中...' : '刷新模型'] })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("select", { "data-testid": "ollama-model-select", value: isCustomModel ? 'custom' : (provider.model || ''), onChange: handleModelSelect, className: cyberSelectClasses, children: [_jsx("option", { value: "", disabled: true, children: "\u8BF7\u9009\u62E9\u6A21\u578B..." }), availableModels.map(model => (_jsx("option", { value: model, children: model }, model))), _jsx("option", { value: "custom", children: "\u81EA\u5B9A\u4E49 / \u624B\u52A8\u8F93\u5165..." })] }), isCustomModel && (_jsxs("div", { className: "animate-in fade-in slide-in-from-top-1", children: [_jsx("input", { type: "text", "data-testid": "ollama-model-id-input", value: provider.model || '', onChange: (e) => handleFieldChange('model', e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "\u8BF7\u8F93\u5165\u6A21\u578B\u540D\uFF08\u5982 llama3:8b\uFF09", autoFocus: true }), _jsxs("p", { className: "mt-1 flex items-center gap-1 text-[9px] text-accent-text", children: [_jsx(AlertCircle, { className: "size-3" }), "\u5DF2\u542F\u7528\u624B\u52A8\u8F93\u5165\uFF0C\u8BF7\u786E\u8BA4\u8BE5\u6A21\u578B\u5DF2\u5728 Ollama \u4E2D\u62C9\u53D6\u3002"] })] })), modelError && (_jsxs("div", { className: "text-[10px] text-rose-300 bg-rose-500/10 px-2 py-1.5 rounded border border-rose-500/20 flex items-start gap-1.5", children: [_jsx(AlertCircle, { className: "size-3 shrink-0 mt-0.5" }), _jsxs("div", { children: [_jsx("p", { className: "font-semibold", children: "\u8FDE\u63A5\u5931\u8D25" }), _jsx("p", { className: "opacity-80", children: modelError })] })] })), !modelError && availableModels.length > 0 && (_jsxs("div", { className: "text-[9px] text-emerald-400/80 flex items-center gap-1 px-1", children: [_jsx(Check, { className: "size-3" }), "\u5DF2\u53D1\u73B0 ", availableModels.length, " \u4E2A\u672C\u5730\u6A21\u578B"] }))] })] })] }) }));
}
