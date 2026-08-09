import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { isCLIProviderType, requiresApiKeyForType, usesBaseUrlForType, } from "../types";
import { cyberInputClassesAlt } from "@/app/components/ui/cyber-input-classes";
// Cyberpunk style input classes - using alt variant with semi-transparent background
const cyberInputClasses = cyberInputClassesAlt;
const parseOptionalPositiveInt = (value) => {
    const trimmed = value.trim();
    if (!trimmed) {
        return undefined;
    }
    const parsed = Number.parseInt(trimmed, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return undefined;
    }
    return parsed;
};
const resolveModelCapabilitySelection = (provider) => {
    const executionProfile = String(provider.execution_profile || "")
        .trim()
        .toLowerCase();
    const toolSchemaProfile = String(provider.tool_schema_profile || "")
        .trim()
        .toLowerCase();
    if (executionProfile === "full" || toolSchemaProfile === "full") {
        return "full";
    }
    if (executionProfile === "compact" ||
        executionProfile === "weak" ||
        executionProfile === "local" ||
        toolSchemaProfile === "slim" ||
        toolSchemaProfile === "compact" ||
        toolSchemaProfile === "weak") {
        return "compact";
    }
    return "auto";
};
export function BaseProviderSettings({ provider, onUpdate, onValidate, children, hideApiKey, hideBaseUrl, }) {
    const [validationResult, setValidationResult] = useState(null);
    const providerNameValue = provider.name == null ? "" : String(provider.name);
    const contextWindowValue = typeof provider.max_context_tokens === "number"
        ? provider.max_context_tokens
        : typeof provider.context_window === "number"
            ? provider.context_window
            : "";
    const maxOutputValue = typeof provider.max_output_tokens === "number"
        ? provider.max_output_tokens
        : typeof provider.max_tokens === "number"
            ? provider.max_tokens
            : "";
    const maxConcurrencyValue = typeof provider.max_concurrency === "number"
        ? provider.max_concurrency
        : "";
    const modelCapabilitySelection = resolveModelCapabilitySelection(provider);
    useEffect(() => {
        const result = onValidate();
        setValidationResult(result);
    }, [provider, onValidate]);
    const handleFieldChange = (field, value) => {
        onUpdate({ [field]: value });
    };
    const handleModelCapabilityChange = (value) => {
        if (value === "compact") {
            onUpdate({ execution_profile: "compact", tool_schema_profile: "slim" });
            return;
        }
        if (value === "full") {
            onUpdate({ execution_profile: "full", tool_schema_profile: "full" });
            return;
        }
        onUpdate({ execution_profile: undefined, tool_schema_profile: undefined });
    };
    const renderValidationStatus = () => {
        if (!validationResult)
            return null;
        if (validationResult.valid) {
            return (_jsxs("div", { className: "flex items-center gap-2 text-emerald-400 text-xs", children: [_jsx(CheckCircle2, { className: "size-3" }), _jsx("span", { children: "\u914D\u7F6E\u6821\u9A8C\u901A\u8FC7" })] }));
        }
        return (_jsxs("div", { className: "space-y-1", children: [validationResult.errors.map((error, index) => (_jsxs("div", { className: "flex items-center gap-2 text-red-400 text-xs", children: [_jsx(AlertTriangle, { className: "size-3" }), _jsx("span", { children: error })] }, index))), validationResult.warnings.map((warning, index) => (_jsxs("div", { className: "flex items-center gap-2 text-yellow-400 text-xs", children: [_jsx(Info, { className: "size-3" }), _jsx("span", { children: warning })] }, index)))] }));
    };
    return (_jsxs("div", { className: "space-y-4", children: [_jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u57FA\u7840\u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u63D0\u4F9B\u5546\u540D\u79F0" }), _jsx("input", { type: "text", value: providerNameValue, onChange: (e) => handleFieldChange("name", e.target.value), className: cyberInputClasses, placeholder: "\u6211\u7684 LLM \u63D0\u4F9B\u5546" })] }), provider.type &&
                        requiresApiKeyForType(provider.type) &&
                        !hideApiKey && (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "API \u5BC6\u94A5" }), _jsx("input", { type: "text", value: provider.api_key || "", onChange: (e) => handleFieldChange("api_key", e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "\u8BF7\u8F93\u5165 API \u5BC6\u94A5" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "API \u5BC6\u94A5\u5C06\u4FDD\u5B58\u5E76\u7528\u4E8E\u9274\u6743" })] })), provider.type && usesBaseUrlForType(provider.type) && !hideBaseUrl && (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u57FA\u7840 URL" }), _jsx("input", { type: "text", value: provider.base_url || "", onChange: (e) => handleFieldChange("base_url", e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "https://api.example.com/v1" })] })), provider.type && isCLIProviderType(provider.type) && (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u547D\u4EE4" }), _jsx("input", { type: "text", value: provider.command || "", onChange: (e) => handleFieldChange("command", e.target.value), className: `${cyberInputClasses} font-mono`, placeholder: "\u4F8B\u5982 codex\u3001gemini" })] })), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u8D85\u65F6\uFF08\u79D2\uFF09" }), _jsx("input", { type: "number", value: provider.timeout || 60, onChange: (e) => handleFieldChange("timeout", parseInt(e.target.value) || 60), className: cyberInputClasses, min: "1", max: "300" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6700\u5927\u5E76\u53D1\u8BF7\u6C42\u6570" }), _jsx("input", { type: "number", "data-testid": "provider-max-concurrency-input", value: maxConcurrencyValue, onChange: (e) => handleFieldChange("max_concurrency", parseOptionalPositiveInt(e.target.value)), className: cyberInputClasses, min: "1", step: "1", placeholder: provider.type === "ollama" ? "默认 1，可显式放大" : "例如 5 或 20" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "Provider \u7684\u7269\u7406/\u8D26\u53F7\u5BB9\u91CF\u4E0A\u9650\uFF1BRole \u5E76\u53D1\u4E0D\u80FD\u7A81\u7834\u8FD9\u4E2A\u4E0A\u9650\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u4E0A\u4E0B\u6587\u7A97\u53E3\u5927\u5C0F\uFF08Context Window Size\uFF09" }), _jsx("input", { type: "number", "data-testid": "provider-max-context-tokens-input", value: contextWindowValue, onChange: (e) => handleFieldChange("max_context_tokens", parseOptionalPositiveInt(e.target.value)), className: cyberInputClasses, min: "1", step: "1", placeholder: "\u4F8B\u5982 200000" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u7528\u4E8E Token Budget \u4E0E\u4E0A\u4E0B\u6587\u538B\u7F29\u9884\u7B97\u8BA1\u7B97\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6700\u5927\u8F93\u51FA Tokens\uFF08Max Output Tokens\uFF09" }), _jsx("input", { type: "number", "data-testid": "provider-max-output-tokens-input", value: maxOutputValue, onChange: (e) => handleFieldChange("max_output_tokens", parseOptionalPositiveInt(e.target.value)), className: cyberInputClasses, min: "1", step: "1", placeholder: "\u4F8B\u5982 8192" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u7528\u4E8E\u63A7\u5236\u4FDD\u7559\u8F93\u51FA\u9884\u7B97\uFF0C\u907F\u514D\u4E0A\u4E0B\u6587\u6324\u5360\u56DE\u590D\u7A7A\u95F4\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6A21\u578B\u80FD\u529B\u753B\u50CF" }), _jsxs("select", { "data-testid": "provider-model-capability-select", value: modelCapabilitySelection, onChange: (e) => handleModelCapabilityChange(e.target.value), className: cyberInputClasses, children: [_jsx("option", { value: "auto", children: "\u81EA\u52A8\u8BC4\u4F30" }), _jsx("option", { value: "compact", children: "\u5F31/\u672C\u5730/\u91CF\u5316\u6A21\u578B" }), _jsx("option", { value: "full", children: "\u5F3A/\u5B8C\u6574\u5DE5\u5177\u6A21\u578B" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u5F71\u54CD\u4E0A\u4E0B\u6587\u5BC6\u5EA6\u4E0E\u5DE5\u5177 schema \u66B4\u9732\u7B56\u7565\u3002" })] })] }), children, _jsx("div", { className: "pt-3 border-t border-white/10", children: renderValidationStatus() })] }));
}
