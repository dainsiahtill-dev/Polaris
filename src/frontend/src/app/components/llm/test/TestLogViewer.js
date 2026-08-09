import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { AlertTriangle, CheckCircle2, Info, ArrowUpRight, ArrowDownLeft } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
const LOG_STYLES = {
    info: { icon: _jsx(Info, { className: "size-3" }), className: 'text-accent-text', label: 'Info' },
    error: { icon: _jsx(AlertTriangle, { className: "size-3" }), className: 'text-status-error', label: 'Error' },
    success: { icon: _jsx(CheckCircle2, { className: "size-3" }), className: 'text-status-success', label: 'Success' },
    request: { icon: _jsx(ArrowUpRight, { className: "size-3" }), className: 'text-status-warning', label: 'Request' },
    response: { icon: _jsx(ArrowDownLeft, { className: "size-3" }), className: 'text-accent-text', label: 'Response' }
};
const renderDetails = (details) => {
    if (details == null)
        return null;
    if (typeof details === 'string')
        return details;
    try {
        return JSON.stringify(details, null, 2);
    }
    catch {
        return String(details);
    }
};
export function TestLogViewer({ logs, className }) {
    const containerRef = useRef(null);
    const [autoScroll, setAutoScroll] = useState(true);
    const rendered = useMemo(() => logs.slice(-200), [logs]);
    useEffect(() => {
        const el = containerRef.current;
        if (!el || !autoScroll)
            return;
        el.scrollTop = el.scrollHeight;
    }, [rendered, autoScroll]);
    useEffect(() => {
        const el = containerRef.current;
        if (!el)
            return;
        const onScroll = () => {
            const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            setAutoScroll(nearBottom);
        };
        el.addEventListener('scroll', onScroll);
        return () => el.removeEventListener('scroll', onScroll);
    }, []);
    return (_jsxs("div", { className: className || '', children: [_jsxs("div", { className: "flex items-center justify-between text-[10px] text-text-dim mb-2", children: [_jsx("span", { children: "\u6D4B\u8BD5\u65E5\u5FD7" }), _jsx("span", { className: "text-[9px]", children: autoScroll ? '自动滚动' : '已暂停滚动' })] }), _jsx("div", { ref: containerRef, className: "soft-inset max-h-56 space-y-2 overflow-auto rounded-lg p-2", children: rendered.length === 0 ? (_jsx("div", { className: "text-[11px] text-text-dim", children: "\u6682\u65E0\u65E5\u5FD7" })) : (rendered.map((log) => {
                    const style = LOG_STYLES[log.type];
                    const detailText = renderDetails(log.details);
                    return (_jsx("div", { className: "text-[11px] text-text-main", children: _jsxs("div", { className: "flex items-start gap-2", children: [_jsx("span", { className: `mt-0.5 ${style.className}`, children: style.icon }), _jsxs("div", { className: "flex-1", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsx("span", { className: "text-[9px] uppercase tracking-wider text-text-dim", children: style.label }), _jsx("span", { className: "text-[9px] text-text-dim", children: new Date(log.timestamp).toLocaleTimeString() })] }), _jsx("div", { className: "text-[11px] text-text-main mt-0.5 whitespace-pre-wrap break-words", children: log.message }), detailText ? (_jsxs("details", { className: "mt-1 text-[10px] text-text-dim", children: [_jsx("summary", { className: "cursor-pointer", children: "\u67E5\u770B\u8BE6\u60C5" }), _jsx("pre", { className: "mt-1 whitespace-pre-wrap break-words text-[10px] text-text-muted font-mono", children: detailText })] })) : null] })] }) }, log.id));
                })) })] }));
}
