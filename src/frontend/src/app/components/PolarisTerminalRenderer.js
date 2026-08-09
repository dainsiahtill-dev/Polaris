import { jsx as _jsx } from "react/jsx-runtime";
export function PolarisTerminalRenderer({ text, className }) {
    return (_jsx("div", { className: className, children: _jsx("pre", { className: "whitespace-pre-wrap break-all font-mono text-xs", children: text }) }));
}
