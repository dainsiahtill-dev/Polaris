import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { BaseProviderSettings } from './BaseProviderSettings';
import { cyberInputClasses, cyberTextareaClasses } from '@/app/components/ui/cyber-input-classes';
export function CodexSDKProviderSettings({ provider, onUpdate, onValidate }) {
    const handleFieldChange = (field, value) => {
        onUpdate({ [field]: value });
    };
    return (_jsx(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, children: _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "Codex SDK \u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u9ED8\u8BA4\u6A21\u578B" }), _jsx("input", { type: "text", value: provider.default_model || '', onChange: (e) => handleFieldChange('default_model', e.target.value), className: cyberInputClasses, placeholder: "gpt-4-codex" })] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6700\u5927\u91CD\u8BD5\u6B21\u6570" }), _jsx("input", { type: "number", value: provider.max_retries ?? 3, onChange: (e) => handleFieldChange('max_retries', parseInt(e.target.value) || 0), className: cyberInputClasses, min: "0", max: "10" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6E29\u5EA6\uFF08Temperature\uFF09" }), _jsx("input", { type: "number", value: provider.temperature ?? 0.2, onChange: (e) => {
                                        // NaN-guard so empty input clears to the default instead of 0.
                                        const parsed = parseFloat(e.target.value);
                                        handleFieldChange('temperature', Number.isNaN(parsed) ? undefined : parsed);
                                    }, className: cyberInputClasses, min: "0", max: "2", step: "any" })] })] }), _jsx("div", { children: _jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: provider.thinking_mode ?? true, onChange: (e) => handleFieldChange('thinking_mode', e.target.checked), className: "rounded border-white/20 bg-[rgba(35,25,14,0.55)]" }), "\u601D\u8003\u6A21\u5F0F"] }) }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "SDK \u53C2\u6570\uFF08JSON\uFF09" }), _jsx("textarea", { value: JSON.stringify(provider.sdk_params || {}, null, 2), onChange: (e) => {
                                try {
                                    const params = JSON.parse(e.target.value);
                                    handleFieldChange('sdk_params', params);
                                }
                                catch {
                                    // ignore invalid JSON
                                }
                            }, className: cyberTextareaClasses, placeholder: '{"organization": "..."}' }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u9644\u52A0 SDK \u5BA2\u6237\u7AEF\u53C2\u6570\uFF0C\u5C06\u5408\u5E76\u5230\u6784\u9020\u53C2\u6570\u4E2D\u3002" })] })] }) }));
}
