import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { cyberInputClasses, cyberTextareaClasses } from '@/app/components/ui/cyber-input-classes';
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
export function OpenAIProviderSettings({ provider, onUpdate, onValidate }) {
    const handleFieldChange = (field, value) => {
        onUpdate({ [field]: value });
    };
    const serializedHeaders = JSON.stringify(provider.headers || {}, null, 2);
    const [headersText, setHeadersText] = useState(serializedHeaders);
    const modelId = typeof provider.model === 'string' && provider.model.trim() !== ''
        ? provider.model
        : typeof provider.default_model === 'string'
            ? provider.default_model
            : '';
    useEffect(() => {
        setHeadersText(serializedHeaders);
    }, [serializedHeaders]);
    return (_jsxs(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, children: [_jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "OpenAI \u517C\u5BB9\u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u8DEF\u5F84" }), _jsx("input", { type: "text", "data-testid": "openai-api-path-input", value: provider.api_path || '/v1/chat/completions', onChange: (e) => handleFieldChange('api_path', e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "/v1/chat/completions" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u7528\u4E8E\u8FDE\u901A\u6027\u6D4B\u8BD5\u7684\u5BF9\u8BDD\u8865\u5168\u63A5\u53E3\u5730\u5740" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u81EA\u5B9A\u4E49\u8BF7\u6C42\u5934\uFF08JSON\uFF09" }), _jsx("textarea", { "data-testid": "openai-custom-headers-input", value: headersText, onChange: (e) => {
                                    const nextValue = e.target.value;
                                    setHeadersText(nextValue);
                                    const parsedHeaders = parseCustomHeadersInput(nextValue);
                                    if (parsedHeaders) {
                                        handleFieldChange('headers', parsedHeaders);
                                    }
                                }, className: `${cyberTextareaClasses} font-mono h-16`, placeholder: '{"Custom-Header": "value"}' }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u652F\u6301 JSON\uFF0C\u6216\u6309\u884C\u586B\u5199 `Key: Value`\u3002" })] })] }), _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u6A21\u578B\u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6A21\u578B ID" }), _jsx("input", { type: "text", "data-testid": "openai-model-id-input", value: modelId, onChange: (e) => handleFieldChange('model', e.target.value), placeholder: "\u8BF7\u8F93\u5165\u517C\u5BB9\u6A21\u578B ID", className: `${cyberInputClasses} font-mono` }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u652F\u6301 OpenAI \u517C\u5BB9\u670D\u52A1\u7684\u7B2C\u4E09\u65B9\u6A21\u578B ID\u3002" })] })] }), _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u9AD8\u7EA7\u53C2\u6570" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6E29\u5EA6\uFF08Temperature\uFF09" }), _jsx("input", { type: "number", value: provider.temperature ?? 0.2, onChange: (e) => {
                                    // ?? + NaN-guard (NOT `|| default`): a temperature of 0 is falsy,
                                    // so `0 || 0.2` silently forced 0.2 and made 0.1 the lowest
                                    // settable value. Empty input clears back to the default.
                                    const parsed = parseFloat(e.target.value);
                                    handleFieldChange('temperature', Number.isNaN(parsed) ? undefined : parsed);
                                }, className: cyberInputClasses, min: "0", max: "2", step: "any" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u91CD\u8BD5\u6B21\u6570" }), _jsx("input", { type: "number", value: provider.retries || 0, onChange: (e) => handleFieldChange('retries', parseInt(e.target.value) || 0), className: cyberInputClasses, min: "0", max: "10" })] })] })] }));
}
