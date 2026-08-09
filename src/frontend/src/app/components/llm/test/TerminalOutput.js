import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useRef, useState } from 'react';
const EVENT_STYLES = {
    command: { prefix: '$', className: 'text-accent-text' },
    stdout: { prefix: '>', className: 'text-status-success' },
    stderr: { prefix: '!', className: 'text-status-warning' },
    response: { prefix: '<', className: 'text-accent-text' },
    result: { prefix: '✓', className: 'text-status-success' },
    error: { prefix: '✗', className: 'text-status-error' }
};
export function TerminalOutput({ events, placeholder, title = '终端输出', heightClassName = 'h-80', className, showHeader = true }) {
    const outputRef = useRef(null);
    const [autoScroll, setAutoScroll] = useState(true);
    useEffect(() => {
        const el = outputRef.current;
        if (!el || !autoScroll)
            return;
        el.scrollTop = el.scrollHeight;
    }, [events, autoScroll]);
    useEffect(() => {
        const el = outputRef.current;
        if (!el)
            return;
        const onScroll = () => {
            const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            setAutoScroll(nearBottom);
        };
        el.addEventListener('scroll', onScroll);
        return () => el.removeEventListener('scroll', onScroll);
    }, []);
    return (_jsxs("div", { className: `space-y-2 ${className || ''}`, children: [showHeader ? (_jsxs("div", { className: "flex items-center justify-between text-[10px] text-text-dim", children: [_jsx("span", { children: title }), _jsx("span", { className: "text-[9px]", children: autoScroll ? '自动滚动' : '已暂停滚动' })] })) : null, _jsx("div", { ref: outputRef, className: `soft-inset rounded-lg p-3 font-mono text-[11px] text-text-main ${heightClassName} overflow-y-auto`, children: events.length === 0 ? (_jsx("div", { className: "text-text-dim", children: placeholder || '$ 准备就绪，点击"测试"按钮开始...' })) : (events.map((event, index) => {
                    const style = EVENT_STYLES[event.type];
                    return (_jsxs("div", { className: "mb-1 whitespace-pre-wrap break-words", children: [_jsxs("span", { className: "text-text-dim", children: ["[", new Date(event.timestamp).toLocaleTimeString(), "]"] }), ' ', _jsxs("span", { className: style.className, children: [style.prefix, " ", event.content] })] }, `${event.timestamp}-${index}`));
                })) })] }));
}
