import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { FileCode, Clock, AlertCircle } from 'lucide-react';
import { FileViewerSkeleton } from './FileViewerSkeleton';
export function FileViewer({ selectedFile, content, mtime, loading, error, badge }) {
    if (!selectedFile) {
        return (_jsx("div", { className: "h-full bg-[var(--ink-indigo)] flex items-center justify-center", children: _jsxs("div", { className: "text-center text-gray-500", children: [_jsx(FileCode, { className: "size-16 mx-auto mb-4 opacity-20" }), _jsx("p", { className: "text-sm", children: "\u9009\u62E9\u5DE6\u4FA7\u6587\u4EF6\u67E5\u770B\u5185\u5BB9" })] }) }));
    }
    const isJsonl = selectedFile.name.endsWith('.jsonl');
    return (_jsxs("div", { className: "h-full bg-[var(--ink-indigo)] flex flex-col", children: [_jsx("div", { className: "px-4 py-3 border-b border-gray-800 bg-[#252526]", children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsx("h3", { className: "text-sm font-semibold text-gray-200", children: selectedFile.name }), _jsx("p", { className: "text-xs text-gray-500 mt-0.5", children: selectedFile.path })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs("div", { className: "flex items-center gap-1 text-xs text-gray-500", children: [_jsx(Clock, { className: "size-3" }), _jsx("span", { children: mtime || '-' })] }), badge ? (_jsx("span", { className: `px-2 py-1 text-xs rounded ${badge.tone === 'green'
                                        ? 'bg-green-500/20 text-green-400'
                                        : badge.tone === 'red'
                                            ? 'bg-red-500/20 text-red-400'
                                            : 'bg-yellow-500/20 text-yellow-400'}`, children: badge.text })) : null] })] }) }), _jsxs("div", { className: "flex-1 overflow-auto", children: [error ? (_jsxs("div", { className: "p-4 text-sm text-red-300 flex items-center gap-2", children: [_jsx(AlertCircle, { className: "size-4" }), _jsx("span", { children: error })] })) : null, loading ? (_jsx(FileViewerSkeleton, {})) : isJsonl ? (_jsx("div", { className: "p-4 space-y-2", children: !content.trim() ? (_jsx("div", { className: "text-sm text-gray-400", children: "(\u7A7A)" })) : (content.split('\n').map((line, idx) => {
                            if (!line.trim())
                                return null;
                            try {
                                const event = JSON.parse(line);
                                return (_jsxs("div", { className: "p-3 bg-gray-800/50 rounded border border-gray-700", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [event.seq !== undefined && (_jsxs("span", { className: "text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400", children: ["seq: ", event.seq] })), event.speaker && (_jsx("span", { className: "text-xs px-2 py-0.5 rounded bg-purple-500/20 text-purple-400", children: event.speaker })), event.kind && (_jsx("span", { className: "text-xs px-2 py-0.5 rounded bg-orange-500/20 text-orange-400", children: event.kind })), event.timestamp && (_jsx("span", { className: "text-xs text-gray-500", children: event.timestamp }))] }), _jsx("pre", { className: "text-xs text-gray-300 font-mono overflow-x-auto", children: JSON.stringify(event, null, 2) })] }, idx));
                            }
                            catch {
                                return null;
                            }
                        })) })) : (_jsx("pre", { className: "p-4 text-sm text-gray-300 font-mono leading-relaxed", children: _jsx("code", { children: content || '(空)' }) }))] })] }));
}
