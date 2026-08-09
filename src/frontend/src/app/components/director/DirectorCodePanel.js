import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * DirectorCodePanel - 代码面板展示组件
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { openPath } from '@/api';
import { FilePlus, FileX, FileEdit, FileCode } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { RealTimeFileDiff } from './RealTimeFileDiff';
import { compareFileEditEventsForCodePanel, hasRenderablePatch, selectDefaultCodePanelEvent, } from './directorCodeEvents';
import { resolveDirectorOpenTarget } from './directorFileActions';
export function DirectorCodePanel({ workspace, fileEditEvents }) {
    const [expandedEventId, setExpandedEventId] = useState(null);
    const [openFileStatus, setOpenFileStatus] = useState({ kind: 'idle', message: null });
    const getOperationIcon = (operation) => {
        switch (operation) {
            case 'create':
                return _jsx(FilePlus, { className: "w-3.5 h-3.5 text-emerald-400" });
            case 'delete':
                return _jsx(FileX, { className: "w-3.5 h-3.5 text-red-400" });
            case 'modify':
            default:
                return _jsx(FileEdit, { className: "w-3.5 h-3.5 text-blue-400" });
        }
    };
    const getOperationLabel = (operation) => {
        switch (operation) {
            case 'create':
                return '创建';
            case 'delete':
                return '删除';
            case 'modify':
                return '修改';
            default:
                return operation;
        }
    };
    const getOperationColor = (operation) => {
        switch (operation) {
            case 'create':
                return 'text-emerald-400';
            case 'delete':
                return 'text-red-400';
            case 'modify':
                return 'text-blue-400';
            default:
                return 'text-slate-400';
        }
    };
    const recentEvents = useMemo(() => [...fileEditEvents].sort(compareFileEditEventsForCodePanel).slice(0, 20), [fileEditEvents]);
    useEffect(() => {
        if (recentEvents.length === 0) {
            setExpandedEventId(null);
            return;
        }
        const defaultEvent = selectDefaultCodePanelEvent(recentEvents);
        setExpandedEventId((previous) => {
            const previousEvent = recentEvents.find((event) => event.id === previous);
            if (!previousEvent) {
                return defaultEvent?.id ?? null;
            }
            if (defaultEvent && !hasRenderablePatch(previousEvent) && hasRenderablePatch(defaultEvent)) {
                return defaultEvent.id;
            }
            return previous;
        });
    }, [recentEvents]);
    const selectedOpenEvent = useMemo(() => recentEvents.find((event) => event.id === expandedEventId) ?? selectDefaultCodePanelEvent(recentEvents), [expandedEventId, recentEvents]);
    const toggleExpand = (eventId) => {
        setExpandedEventId((prev) => (prev === eventId ? null : eventId));
    };
    const handleOpenFile = useCallback(async () => {
        const target = resolveDirectorOpenTarget(workspace, selectedOpenEvent?.filePath);
        if (!target) {
            setOpenFileStatus({ kind: 'error', message: '没有可打开的工作区文件' });
            return;
        }
        setOpenFileStatus({ kind: 'loading', message: `正在打开 ${selectedOpenEvent?.filePath || target}` });
        try {
            const result = await openPath(target);
            if (!result.ok) {
                setOpenFileStatus({ kind: 'error', message: result.error || '打开文件失败' });
                return;
            }
            setOpenFileStatus({ kind: 'success', message: `已请求打开 ${selectedOpenEvent?.filePath || target}` });
        }
        catch (error) {
            setOpenFileStatus({
                kind: 'error',
                message: error instanceof Error ? error.message : '打开文件失败',
            });
        }
    }, [selectedOpenEvent, workspace]);
    return (_jsxs("div", { "data-testid": "director-code-panel", className: "h-full flex flex-col", children: [_jsxs("div", { className: "h-12 flex items-center justify-between px-4 border-b border-white/5", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("h2", { className: "text-sm font-medium text-slate-200", children: "\u5B9E\u65F6\u4EE3\u7801\u53D8\u66F4" }), fileEditEvents.length > 0 && (_jsxs("span", { className: "text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-400", children: [fileEditEvents.length, " \u4E2A\u6587\u4EF6"] }))] }), _jsx("div", { className: "flex items-center gap-2", children: _jsxs(Button, { variant: "ghost", size: "sm", onClick: () => { void handleOpenFile(); }, disabled: !selectedOpenEvent || openFileStatus.kind === 'loading', "data-testid": "director-code-open-file", title: selectedOpenEvent?.filePath ? `打开 ${selectedOpenEvent.filePath}` : '没有可打开的文件', className: "text-slate-400", children: [_jsx(FileCode, { className: "w-4 h-4 mr-1.5" }), openFileStatus.kind === 'loading' ? '打开中' : '打开文件'] }) })] }), openFileStatus.message ? (_jsx("div", { className: cn('border-b px-4 py-1.5 text-[11px]', openFileStatus.kind === 'error'
                    ? 'border-amber-500/20 bg-amber-500/10 text-amber-100'
                    : 'border-emerald-500/20 bg-emerald-500/10 text-emerald-100'), "data-testid": "director-code-open-file-evidence", children: openFileStatus.message })) : null, _jsxs("div", { className: "flex-1 overflow-hidden flex", children: [_jsx("div", { className: "flex-1 overflow-auto p-4", children: recentEvents.length === 0 ? (_jsxs("div", { "data-testid": "director-code-empty", className: "h-full flex flex-col items-center justify-center text-slate-500", children: [_jsx(FileCode, { className: "w-12 h-12 mb-4 text-indigo-500/30" }), _jsx("p", { children: "\u7B49\u5F85\u4EE3\u7801\u53D8\u66F4..." }), _jsx("p", { className: "text-xs mt-2 opacity-70", children: "Director \u6267\u884C\u65F6\u5C06\u5B9E\u65F6\u663E\u793A\u6587\u4EF6\u4FEE\u6539" })] })) : (_jsx("div", { "data-testid": "director-code-event-list", className: "space-y-2", children: recentEvents.map((event, index) => (_jsxs("div", { children: [_jsx("div", { "data-testid": "director-code-event-row", "data-file-path": event.filePath, "data-event-id": event.id, className: cn('p-3 rounded-xl border transition-all cursor-pointer', index === 0 ? 'bg-indigo-500/10 border-indigo-500/30' : 'bg-white/5 border-white/5 hover:border-white/10', expandedEventId === event.id && 'ring-1 ring-indigo-500/30'), onClick: () => toggleExpand(event.id), children: _jsxs("div", { className: "flex items-start gap-3", children: [_jsx("div", { className: "mt-0.5", children: getOperationIcon(event.operation) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2 flex-wrap", children: [_jsx("span", { className: "text-xs font-mono text-slate-300 truncate flex-1", title: event.filePath, children: event.filePath }), _jsx("span", { className: cn('text-[10px] px-1.5 py-0.5 rounded bg-white/10', getOperationColor(event.operation)), children: getOperationLabel(event.operation) }), hasRenderablePatch(event) && (_jsx("span", { className: "text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-400", children: "Diff" }))] }), _jsxs("div", { className: "mt-1 flex items-center gap-3 text-[10px] text-slate-500", children: [_jsxs("span", { children: [event.contentSize, " bytes"] }), event.taskId && _jsxs("span", { className: "text-slate-600", children: ["\u4EFB\u52A1: ", event.taskId.slice(0, 8)] }), _jsx("span", { className: "text-slate-600", children: new Date(event.timestamp).toLocaleTimeString() }), hasRenderablePatch(event) && (_jsx("span", { className: "text-cyan-400", children: expandedEventId === event.id ? '▼ 收起' : '▶ 展开 Diff' }))] })] })] }) }), expandedEventId === event.id && hasRenderablePatch(event) && (_jsx("div", { className: "mt-2", children: _jsx(RealTimeFileDiff, { filePath: event.filePath, operation: event.operation, patch: event.patch, compact: true }) }))] }, event.id))) })) }), _jsxs("div", { className: "w-48 border-l border-white/5 p-4 bg-slate-950/30", children: [_jsx("h3", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-3", children: "\u53D8\u66F4\u7EDF\u8BA1" }), _jsxs("div", { className: "space-y-2", children: [_jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20", children: [_jsxs("span", { className: "text-xs text-emerald-400 flex items-center gap-1.5", children: [_jsx(FilePlus, { className: "w-3 h-3" }), "\u521B\u5EFA"] }), _jsx("span", { className: "text-xs font-mono text-emerald-300", children: fileEditEvents.filter((e) => e.operation === 'create').length })] }), _jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-blue-500/10 border border-blue-500/20", children: [_jsxs("span", { className: "text-xs text-blue-400 flex items-center gap-1.5", children: [_jsx(FileEdit, { className: "w-3 h-3" }), "\u4FEE\u6539"] }), _jsx("span", { className: "text-xs font-mono text-blue-300", children: fileEditEvents.filter((e) => e.operation === 'modify').length })] }), _jsxs("div", { className: "flex items-center justify-between p-2 rounded-lg bg-red-500/10 border border-red-500/20", children: [_jsxs("span", { className: "text-xs text-red-400 flex items-center gap-1.5", children: [_jsx(FileX, { className: "w-3 h-3" }), "\u5220\u9664"] }), _jsx("span", { className: "text-xs font-mono text-red-300", children: fileEditEvents.filter((e) => e.operation === 'delete').length })] })] }), _jsxs("div", { className: "mt-6 pt-4 border-t border-white/5", children: [_jsx("h3", { className: "text-[10px] uppercase tracking-wider text-slate-500 mb-2", children: "\u5DE5\u4F5C\u533A" }), _jsx("p", { className: "text-xs text-slate-400 truncate", title: workspace, children: workspace })] })] })] })] }));
}
