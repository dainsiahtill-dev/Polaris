import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
export function CyberpunkTestAnimation({ progress = 0, status }) {
    if (status !== 'running') {
        return null;
    }
    return (_jsxs("div", { className: "relative inline-flex items-center gap-2", children: [_jsxs("div", { className: "relative flex items-center gap-1 px-3 py-1", children: [_jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_0_3px_rgba(47,127,120,0.12)]" }), _jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-accent/70" }), _jsx("span", { className: "h-1.5 w-1.5 rounded-full bg-accent/40" })] }), _jsx("div", { className: "relative flex h-6 items-center overflow-hidden", children: _jsx("span", { className: "relative font-mono text-sm text-accent-text", children: "\u626B\u63CF\u4E2D" }) }), progress > 0 && (_jsx("div", { className: "soft-inset relative h-1.5 w-24 overflow-hidden rounded-full", children: _jsx("div", { className: "soft-progress absolute inset-0 transition-all duration-300", style: {
                        width: `${progress}%`
                    } }) }))] }));
}
export function CyberpunkStatusBorder({ children, status, className = '' }) {
    const statusClass = {
        running: 'border-accent/45 shadow-[0_14px_34px_rgba(47,127,120,0.16)]',
        success: 'border-status-success/45 shadow-[0_14px_34px_rgba(40,122,85,0.14)]',
        failed: 'border-status-error/45 shadow-[0_14px_34px_rgba(182,63,73,0.14)]',
    }[status];
    if (status === 'running') {
        return (_jsx("div", { className: `relative ${className}`, children: _jsx("div", { className: `soft-raised relative rounded-lg ${statusClass}`, children: children }) }));
    }
    return (_jsx("div", { className: `relative ${className}`, children: _jsx("div", { className: `soft-panel-subtle relative rounded-lg ${statusClass}`, children: children }) }));
}
export function CyberpunkCard({ children, status, className = '', ...rest }) {
    const statusColors = {
        running: {
            border: 'border-accent/45',
            bg: 'bg-accent/10',
            shadow: 'shadow-[0_14px_34px_rgba(47,127,120,0.14)]',
        },
        success: {
            border: 'border-status-success/45',
            bg: 'bg-status-success/10',
            shadow: 'shadow-[0_14px_34px_rgba(40,122,85,0.12)]',
        },
        failed: {
            border: 'border-status-error/45',
            bg: 'bg-status-error/10',
            shadow: 'shadow-[0_14px_34px_rgba(182,63,73,0.12)]',
        },
        unknown: {
            border: 'border-status-warning/45',
            bg: 'bg-status-warning/10',
            shadow: 'shadow-[0_14px_34px_rgba(199,130,24,0.12)]',
        },
    };
    const colors = statusColors[status];
    return (_jsx("div", { className: `soft-panel-subtle rounded-lg border ${colors.border} ${colors.bg} ${colors.shadow} ${className}`, ...rest, children: children }));
}
export function CyberpunkGlitchText({ text, status, className = '' }) {
    if (status === 'success') {
        return (_jsx("span", { className: `font-mono text-status-success ${className}`, children: text }));
    }
    if (status === 'failed') {
        return (_jsx("span", { className: `font-mono text-status-error ${className}`, children: text }));
    }
    if (status === 'running') {
        return (_jsx("span", { className: `font-mono text-accent-text ${className}`, children: text }));
    }
    return (_jsx("span", { className: `font-mono text-status-warning ${className}`, children: text }));
}
