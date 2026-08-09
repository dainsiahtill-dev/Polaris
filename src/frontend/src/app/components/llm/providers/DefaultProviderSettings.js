import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { BaseProviderSettings } from './BaseProviderSettings';
export function DefaultProviderSettings({ provider, onUpdate, onValidate }) {
    return (_jsx(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, children: _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u901A\u7528\u63D0\u4F9B\u5546\u8BBE\u7F6E" }), _jsx("div", { className: "bg-black/30 rounded-lg p-3 border border-white/10", children: _jsx("p", { className: "text-xs text-text-dim", children: "\u5F53\u524D\u63D0\u4F9B\u5546\u4F7F\u7528\u9ED8\u8BA4\u914D\u7F6E\uFF1B\u53EF\u7528\u7684\u4E13\u5C5E\u53C2\u6570\u53D6\u51B3\u4E8E\u63D0\u4F9B\u5546\u7C7B\u578B\u3002" }) })] }) }));
}
