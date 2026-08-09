import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { FileText, Search, Clock, AlertCircle, ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';
export function MemoPanel({ items, selected, content, mtime, loading, error, onSelect, collapsed, onToggle, }) {
    const [query, setQuery] = useState('');
    const filtered = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q)
            return items;
        return items.filter((item) => {
            const haystack = [
                item.name,
                item.summary,
                item.task_id,
                item.task_title,
                item.status,
                item.run_id,
            ]
                .filter(Boolean)
                .join(' ')
                .toLowerCase();
            return haystack.includes(q);
        });
    }, [items, query]);
    return (_jsxs("div", { "data-testid": "memo-panel", className: "flex h-full min-w-0 flex-col overflow-hidden border-l border-gray-800 bg-[var(--ink-indigo)]", children: [_jsxs("div", { className: "flex min-w-0 items-center justify-between gap-3 border-b border-gray-800 bg-[#252526] px-4 py-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(FileText, { className: "size-4 text-blue-400" }), _jsx("h2", { className: "truncate text-sm font-semibold text-gray-300", children: "PM Memos" })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-3 text-xs text-gray-500", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-1", children: [_jsx(Clock, { className: "size-3" }), _jsx("span", { className: "max-w-36 truncate", children: mtime || selected?.mtime || '-' })] }), _jsx("button", { type: "button", onClick: onToggle, className: "p-1 text-gray-400 hover:text-white transition-colors", "aria-label": collapsed ? '展开备忘录面板' : '收起备忘录面板', children: _jsx("div", { className: `transform transition-transform ${collapsed ? '-rotate-90' : 'rotate-0'}`, children: _jsx(ChevronDown, { className: "size-3" }) }) })] })] }), collapsed ? null : (_jsxs("div", { className: "border-b border-gray-800 p-3", children: [_jsxs("div", { className: "relative", children: [_jsx(Search, { className: "absolute left-2.5 top-2.5 size-3.5 text-gray-500" }), _jsx("input", { value: query, onChange: (e) => setQuery(e.target.value), placeholder: "\u641C\u7D22\u5907\u5FD8\u5F55\uFF08\u4EFB\u52A1/\u6458\u8981/ID\uFF09", className: "w-full bg-[#151515] text-gray-300 px-8 py-2 rounded border border-gray-700 text-xs focus:outline-none focus:border-blue-500" })] }), _jsxs("div", { className: "mt-2 text-[11px] text-gray-500", children: ["\u5171 ", filtered.length, " \u6761"] })] })), collapsed ? null : (_jsxs("div", { className: "flex min-h-0 min-w-0 flex-1", children: [_jsx("div", { className: "w-56 shrink-0 overflow-y-auto border-r border-gray-800", children: filtered.length === 0 ? (_jsx("div", { className: "p-3 text-xs text-gray-500", children: "(\u6682\u65E0\u5907\u5FD8\u5F55)" })) : (filtered.map((item) => {
                            const isActive = selected?.path === item.path;
                            return (_jsxs("button", { onClick: () => onSelect(item), className: `w-full min-w-0 border-b border-gray-800/60 px-3 py-2 text-left hover:bg-white/5 ${isActive ? 'bg-blue-500/10' : ''}`, children: [_jsx("div", { className: "text-xs text-gray-300 truncate", children: item.task_title || item.name }), _jsx("div", { className: "text-[11px] text-gray-500 truncate", children: item.summary || item.task_id || item.run_id || '' })] }, item.path));
                        })) }), _jsxs("div", { "data-testid": "memo-panel-body", className: "min-w-0 flex-1 overflow-auto", children: [error ? (_jsxs("div", { className: "flex items-center gap-2 p-4 text-sm text-red-300", children: [_jsx(AlertCircle, { className: "size-4" }), _jsx("span", { className: "min-w-0 break-words", children: error })] })) : null, loading ? (_jsx("div", { className: "p-4 text-sm text-gray-300", children: "\u52A0\u8F7D\u4E2D..." })) : (_jsx("pre", { "data-testid": "memo-panel-content", className: "whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed text-gray-300", children: _jsx("code", { children: content || '(空)' }) }))] })] }))] }));
}
