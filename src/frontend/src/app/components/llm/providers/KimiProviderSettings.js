import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useCallback, useEffect } from 'react';
import { Key } from 'lucide-react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { cyberInputClasses, cyberTextareaCompactClasses } from '@/app/components/ui/cyber-input-classes';
const parseCustomHeadersInput = (rawValue) => {
    const trimmed = rawValue.trim();
    if (!trimmed) {
        return {};
    }
    try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            const normalized = {};
            Object.entries(parsed).forEach(([key, value]) => {
                if (!key || value === undefined || value === null)
                    return;
                normalized[String(key)] = String(value);
            });
            return normalized;
        }
    }
    catch {
        // Fall through to line-based parsing.
    }
    if (trimmed.includes('{') || trimmed.includes('}')) {
        return null;
    }
    const lines = trimmed
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean);
    if (lines.length === 0) {
        return {};
    }
    const parsedHeaders = {};
    for (const line of lines) {
        const separatorIndex = line.indexOf(':');
        if (separatorIndex <= 0) {
            return null;
        }
        const key = line.slice(0, separatorIndex).trim();
        const value = line.slice(separatorIndex + 1).trim();
        if (!key) {
            return null;
        }
        parsedHeaders[key] = value;
    }
    return parsedHeaders;
};
const cyberTextareaClasses = cyberTextareaCompactClasses;
// Predefined Kimi models for quick selection
const KIMI_MODELS = [
    { id: 'kimi-k2.5', context: '256k', description: 'Kimi 迄今最智能的模型，在 Agent、代码、视觉理解及一系列通用智能任务上取得开源 SoTA 表现。同时 Kimi K2.5 也是 Kimi 迄今最全能的模型，原生的多模态架构设计，同时支持视觉与文本输入、思考与非思考模式、对话与 Agent 任务。' },
    { id: 'kimi-k2-0905-preview', context: '256k', description: '在 0711 版本基础上增强了 Agentic Coding 能力、前端代码美观度和实用性、以及上下文理解能力' },
    { id: 'kimi-k2-0711-preview', context: '128k', description: 'MoE 架构基础模型，总参数 1T，激活参数 32B。具备超强代码和 Agent 能力。' },
    { id: 'kimi-k2-turbo-preview', context: '256k', description: 'K2 的高速版本，对标最新版本(0905)。输出速度提升至每秒 60-100 tokens' },
    { id: 'kimi-k2-thinking', context: '256k', description: 'K2 长思考模型，支持  上下文，支持多步工具调用与思考，擅长解决更复杂的问题' },
    { id: 'kimi-k2-thinking-turbo', context: '256k', description: 'K2 长思考模型的高速版本，擅长深度推理，输出速度提升至每秒 60-100 tokens' }
];
function KimiApiKeyInput({ value, onChange, placeholder }) {
    const handleChange = useCallback((e) => {
        onChange(e.target.value);
    }, [onChange]);
    return (_jsxs("div", { children: [_jsxs("label", { className: "block text-xs text-text-muted mb-1 flex items-center gap-1", children: [_jsx(Key, { className: "size-3" }), "API Key"] }), _jsx("input", { type: "text", "data-testid": "kimi-api-key-input", value: value ?? '', onChange: handleChange, placeholder: placeholder || 'sk-...', className: `${cyberInputClasses} font-mono`, autoComplete: "off", spellCheck: false }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "API Key\u7528\u4E8E\u8EAB\u4EFD\u9A8C\u8BC1\uFF0C\u8BF7\u59A5\u5584\u4FDD\u7BA1" })] }));
}
export function KimiProviderSettings({ provider, onUpdate, onValidate }) {
    const serializedHeaders = JSON.stringify(provider.headers || {}, null, 2);
    const [headersText, setHeadersText] = useState(serializedHeaders);
    useEffect(() => {
        setHeadersText(serializedHeaders);
    }, [serializedHeaders]);
    const setFieldValue = useCallback((field, value) => {
        onUpdate({ [field]: value });
    }, [onUpdate]);
    const handleFieldChange = useCallback((field, value) => {
        setFieldValue(field, value);
    }, [setFieldValue]);
    // Get current model value
    const currentModel = provider.model || provider.default_model || 'kimi-k2-thinking';
    return (_jsxs(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, hideApiKey: true, hideBaseUrl: true, children: [_jsxs("div", { className: "space-y-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "Kimi API \u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u57FA\u7840URL" }), _jsx("input", { type: "text", "data-testid": "kimi-base-url-input", value: provider.base_url || '', onChange: (e) => setFieldValue('base_url', e.target.value), placeholder: "https://api.moonshot.cn/v1", className: `${cyberInputClasses} font-mono` }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "Moonshot AI \u5B98\u65B9 API \u7AEF\u70B9" })] }), _jsx(KimiApiKeyInput, { value: provider.api_key, onChange: (value) => setFieldValue('api_key', value), placeholder: "sk-..." }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u8DEF\u5F84" }), _jsx("input", { type: "text", "data-testid": "kimi-api-path-input", value: provider.api_path || '', onChange: (e) => setFieldValue('api_path', e.target.value), placeholder: "/v1/chat/completions", className: `${cyberInputClasses} font-mono` }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u5BF9\u8BDD\u8865\u5168 API \u8DEF\u5F84\uFF08OpenAI \u517C\u5BB9\u683C\u5F0F\uFF09" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u81EA\u5B9A\u4E49\u8BF7\u6C42\u5934\uFF08JSON\uFF09" }), _jsx("textarea", { "data-testid": "kimi-custom-headers-input", value: headersText, onChange: (e) => {
                                    const nextValue = e.target.value;
                                    setHeadersText(nextValue);
                                    const parsedHeaders = parseCustomHeadersInput(nextValue);
                                    if (parsedHeaders) {
                                        handleFieldChange('headers', parsedHeaders);
                                    }
                                }, className: cyberTextareaClasses, placeholder: '{"x-test-header":"abc123"}' }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u652F\u6301 JSON \u6216\u6309\u884C\u586B\u5199 `Key: Value`\uFF0C\u7528\u4E8E\u9644\u52A0\u9274\u6743\u6216\u8DEF\u7531\u5B57\u6BB5\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6A21\u578B" }), _jsx("input", { type: "text", "data-testid": "kimi-model-id-input", value: currentModel, onChange: (e) => handleFieldChange('model', e.target.value), placeholder: "kimi-k2-thinking", className: `${cyberInputClasses} font-mono` }), _jsx("div", { className: "mt-2 flex flex-wrap gap-2", children: KIMI_MODELS.map((model) => (_jsxs("button", { type: "button", onClick: () => handleFieldChange('model', model.id), className: `text-[9px] px-2 py-1 rounded border transition-colors ${currentModel === model.id
                                        ? 'bg-slate-500/20 border-slate-500/50 text-slate-200'
                                        : 'bg-[rgba(35,25,14,0.45)] border-white/10 hover:border-white/20 text-text-dim'}`, children: [model.id, _jsxs("span", { className: "ml-1 opacity-70", children: ["(", model.context, ")"] })] }, model.id))) }), _jsx("p", { className: "text-[9px] text-text-dim mt-2", children: "\u652F\u6301\u591A\u8F6E\u5BF9\u8BDD\u3001\u6D41\u5F0F\u8F93\u51FA\u3001\u591A\u6A21\u6001\u8F93\u5165\uFF08\u6587\u672C\u3001\u56FE\u7247\u3001\u89C6\u9891\uFF09" })] })] }), _jsxs("div", { className: "space-y-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u6A21\u578B\u53C2\u6570" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6E29\u5EA6\uFF08Temperature\uFF0C0-1\uFF09" }), _jsx("input", { type: "number", value: provider.temperature ?? '', onChange: (e) => setFieldValue('temperature', e.target.value === '' ? undefined : parseFloat(e.target.value)), placeholder: "1.0", className: cyberInputClasses, min: 0, max: 1, step: "any" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u5F71\u54CD\u8F93\u51FA\u968F\u673A\u6027\uFF0C\u503C\u8D8A\u9AD8\u8D8A\u968F\u673A\uFF0C\u9ED8\u8BA41.0" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "Top P\uFF08\u6838\u91C7\u6837\uFF0C0-1\uFF09" }), _jsx("input", { type: "number", value: provider.top_p ?? '', onChange: (e) => setFieldValue('top_p', e.target.value === '' ? undefined : parseFloat(e.target.value)), placeholder: "0.95", className: cyberInputClasses, min: 0, max: 1, step: "0.01" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u6838\u91C7\u6837\u9608\u503C\uFF0C\u9ED8\u8BA40.95" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { type: "checkbox", id: "kimi-stream", checked: provider.streaming ?? false, onChange: (e) => handleFieldChange('streaming', e.target.checked), className: "rounded border-white/10 bg-[rgba(35,25,14,0.45)]" }), _jsx("label", { htmlFor: "kimi-stream", className: "text-xs text-text-main", children: "\u542F\u7528\u6D41\u5F0F\u4F20\u8F93" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u5F00\u542F\u540E\u54CD\u5E94\u5C06\u5206\u6279\u8FD4\u56DE\uFF0C\u9002\u5408\u5B9E\u65F6\u5BF9\u8BDD\u573A\u666F" })] }), _jsxs("div", { className: "space-y-4", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "Kimi \u6A21\u578B\u4FE1\u606F" }), _jsx("div", { className: "bg-[rgba(35,25,14,0.45)] rounded-lg p-4 border border-white/10", children: _jsxs("div", { className: "space-y-3 text-xs", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("div", { className: "w-2 h-2 rounded-full bg-slate-400" }), _jsx("span", { className: "text-text-main font-medium", children: "Moonshot AI (Kimi)" }), _jsx("span", { className: "text-text-dim", children: "\u5B98\u65B9\u5927\u8BED\u8A00\u6A21\u578B" })] }), _jsxs("div", { className: "space-y-2 text-text-dim", children: [_jsx("p", { children: "Kimi \u662F Moonshot AI \u63A8\u51FA\u7684\u7CFB\u5217\u5927\u8BED\u8A00\u6A21\u578B\uFF0C\u5177\u5907\u5F3A\u5927\u7684\u901A\u7528\u667A\u80FD\u80FD\u529B\u548C\u8D85\u5927\u4E0A\u4E0B\u6587\u7A97\u53E3\uFF0C\u9002\u7528\u4E8E\u5BF9\u8BDD\u3001\u4EE3\u7801\u751F\u6210\u3001\u89C6\u89C9\u7406\u89E3\u7B49\u591A\u79CD\u4EFB\u52A1\u3002" }), _jsx("p", { children: "\u2022 kimi-k2.5\uFF1AKimi \u8FC4\u4ECA\u6700\u667A\u80FD\u7684\u6A21\u578B\uFF0C\u5728 Agent\u3001\u4EE3\u7801\u3001\u89C6\u89C9\u7406\u89E3\u53CA\u4E00\u7CFB\u5217\u901A\u7528\u667A\u80FD\u4EFB\u52A1\u4E0A\u53D6\u5F97\u5F00\u6E90 SoTA \u8868\u73B0\u3002\u540C\u65F6 Kimi K2.5 \u4E5F\u662F Kimi \u8FC4\u4ECA\u6700\u5168\u80FD\u7684\u6A21\u578B\uFF0C\u539F\u751F\u7684\u591A\u6A21\u6001\u67B6\u6784\u8BBE\u8BA1\uFF0C\u540C\u65F6\u652F\u6301\u89C6\u89C9\u4E0E\u6587\u672C\u8F93\u5165\u3001\u601D\u8003\u4E0E\u975E\u601D\u8003\u6A21\u5F0F\u3001\u5BF9\u8BDD\u4E0E Agent \u4EFB\u52A1\u3002" }), _jsx("p", { children: "\u2022 kimi-k2-0905-preview\uFF1A\u5728 0711 \u7248\u672C\u57FA\u7840\u4E0A\u589E\u5F3A\u4E86 Agentic Coding \u80FD\u529B\u3001\u524D\u7AEF\u4EE3\u7801\u7F8E\u89C2\u5EA6\u548C\u5B9E\u7528\u6027\u3001\u4EE5\u53CA\u4E0A\u4E0B\u6587\u7406\u89E3\u80FD\u529B" }), _jsx("p", { children: "\u2022 kimi-k2-0711-preview\uFF1AMoE \u67B6\u6784\u57FA\u7840\u6A21\u578B\uFF0C\u603B\u53C2\u6570 1T\uFF0C\u6FC0\u6D3B\u53C2\u6570 32B\u3002\u5177\u5907\u8D85\u5F3A\u4EE3\u7801\u548C Agent \u80FD\u529B\u3002" }), _jsx("p", { children: "\u2022 kimi-k2-thinking\uFF1AK2 \u957F\u601D\u8003\u6A21\u578B\uFF0C\u652F\u6301 256K \u4E0A\u4E0B\u6587\u7A97\u53E3" }), _jsx("p", { children: "\u2022 kimi-k2-turbo-preview\uFF1AK2 \u7684\u9AD8\u901F\u7248\u672C\uFF0C\u652F\u6301 256K \u4E0A\u4E0B\u6587\u7A97\u53E3" }), _jsx("p", { children: "\u2022 \u652F\u6301\u591A\u8F6E\u5BF9\u8BDD\u3001\u6D41\u5F0F\u8F93\u51FA\u3001\u591A\u6A21\u6001\u8F93\u5165" })] }), _jsx("div", { className: "pt-2 border-t border-white/10", children: _jsxs("p", { className: "text-[9px] text-text-dim", children: ["\u5B98\u65B9\u6587\u6863\uFF1A", _jsx("a", { href: "https://platform.moonshot.cn/docs/api/chat", target: "_blank", rel: "noopener noreferrer", className: "text-slate-300 hover:text-slate-200 ml-1", children: "Kimi API \u6587\u6863" })] }) })] }) })] })] }));
}
