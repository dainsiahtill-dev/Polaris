import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback } from 'react';
import { Key } from 'lucide-react';
import { cyberInputClassesAlt } from '@/app/components/ui/cyber-input-classes';
// Cyberpunk style input classes - using alt variant with semi-transparent background
const cyberInputClasses = cyberInputClassesAlt;
/**
 * 简化的提供商输入组件，作为受控组件交由上层状态管理
 */
export function ProviderInput({ value, onChange, placeholder, type = 'text', className = '', disabled = false, autoComplete = 'off', spellCheck = false, min, max, step, debugLabel }) {
    const handleChange = useCallback((e) => {
        const newValue = e.target.value;
        onChange(newValue);
    }, [onChange]);
    return (_jsx("input", { type: type, value: value ?? '', onChange: handleChange, className: `${cyberInputClasses} ${className}`, placeholder: placeholder, disabled: disabled, autoComplete: autoComplete, spellCheck: spellCheck, min: min, max: max, step: step, "data-debug-label": debugLabel }));
}
export function ApiKeyInput({ apiKey, onChange, placeholder = "sk-...", debugLabel = 'api_key' }) {
    return (_jsxs("div", { children: [_jsxs("label", { className: "block text-xs text-text-muted mb-1 flex items-center gap-1", children: [_jsx(Key, { className: "size-3" }), "API Key"] }), _jsx(ProviderInput, { value: apiKey, onChange: onChange, type: "text", placeholder: placeholder, className: "font-mono", debugLabel: debugLabel }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "API Key\u7528\u4E8E\u8EAB\u4EFD\u9A8C\u8BC1\uFF0C\u8BF7\u59A5\u5584\u4FDD\u7BA1" })] }));
}
export function UrlInput({ value, onChange, placeholder, label = "URL", description, debugLabel = 'url' }) {
    return (_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: label }), _jsx(ProviderInput, { value: value, onChange: onChange, type: "url", placeholder: placeholder, className: "font-mono", debugLabel: debugLabel }), description && (_jsx("p", { className: "text-[9px] text-text-dim mt-1", children: description }))] }));
}
export function TextInput({ value, onChange, placeholder, label, description, debugLabel = 'text' }) {
    return (_jsxs("div", { children: [label && (_jsx("label", { className: "block text-xs text-text-muted mb-1", children: label })), _jsx(ProviderInput, { value: value, onChange: onChange, type: "text", placeholder: placeholder, className: "font-mono", debugLabel: debugLabel }), description && (_jsx("p", { className: "text-[9px] text-text-dim mt-1", children: description }))] }));
}
export function NumberInput({ value, onChange, placeholder, label, description, min, max, step, debugLabel = 'number' }) {
    return (_jsxs("div", { children: [label && (_jsx("label", { className: "block text-xs text-text-muted mb-1", children: label })), _jsx(ProviderInput, { value: value?.toString() || '', onChange: (val) => onChange(val === '' ? undefined : parseFloat(val)), type: "number", placeholder: placeholder, min: min, max: max, step: step, debugLabel: debugLabel }), description && (_jsx("p", { className: "text-[9px] text-text-dim mt-1", children: description }))] }));
}
