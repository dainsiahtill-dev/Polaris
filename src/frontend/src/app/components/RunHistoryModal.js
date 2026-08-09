import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import { Dialog, DialogContent, DialogHeader, DialogTitle, } from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { History, CheckCircle, XCircle, Clock, FileText, ArrowRight } from 'lucide-react';
import { LogsModal } from '@/app/components/LogsModal';
import { UI_TERMS } from '@/app/constants/uiTerminology';
export function RunHistoryModal({ isOpen, onClose }) {
    const [runs, setRuns] = useState([]);
    const [loading, setLoading] = useState(false);
    const [selectedRun, setSelectedRun] = useState(null);
    const [showLogs, setShowLogs] = useState(false);
    useEffect(() => {
        if (isOpen) {
            setLoading(true);
            apiFetch('/history/runs')
                .then((res) => res.json())
                .then((data) => {
                setRuns(data.runs || []);
                if (data.runs && data.runs.length > 0 && !selectedRun) {
                    setSelectedRun(data.runs[0]);
                }
            })
                .catch(console.error)
                .finally(() => setLoading(false));
        }
    }, [isOpen]);
    const handleRunClick = (run) => {
        setSelectedRun(run);
    };
    return (_jsxs(_Fragment, { children: [_jsx(Dialog, { open: isOpen, onOpenChange: onClose, children: _jsxs(DialogContent, { className: "soft-panel max-w-5xl h-[80vh] text-text-main flex flex-col p-0 gap-0", children: [_jsx(DialogHeader, { className: "soft-panel-subtle p-6 border-b", children: _jsxs(DialogTitle, { className: "text-xl font-semibold flex items-center gap-2", children: [_jsx(History, { className: "h-5 w-5 text-accent" }), UI_TERMS.nouns.history] }) }), _jsxs("div", { className: "flex-1 flex overflow-hidden", children: [_jsx("div", { className: "w-1/3 border-r border-border flex flex-col", children: _jsx(ScrollArea, { className: "flex-1", children: _jsx("div", { className: "p-2 space-y-1", children: loading ? (_jsx("div", { className: "text-center text-text-dim py-8", children: "Loading history..." })) : runs.length === 0 ? (_jsx("div", { className: "text-center text-text-dim py-8", children: "No history yet." })) : (runs.map((run) => (_jsxs("div", { onClick: () => handleRunClick(run), className: `p-3 rounded-lg cursor-pointer transition-colors border ${selectedRun?.id === run.id
                                                    ? 'soft-raised border-border-glow'
                                                    : 'bg-transparent border-transparent hover:bg-white/60'}`, children: [_jsxs("div", { className: "flex items-center justify-between mb-1", children: [_jsx("span", { className: `font-mono text-xs ${selectedRun?.id === run.id ? 'text-accent-text' : 'text-text-muted'}`, children: run.id }), run.status === 'success' ? (_jsx(CheckCircle, { className: "h-3 w-3 text-emerald-400" })) : run.status === 'fail' ? (_jsx(XCircle, { className: "h-3 w-3 text-red-400" })) : (_jsx("span", { className: "text-[10px] text-text-dim", children: run.status || '?' }))] }), _jsx("div", { className: "text-xs text-text-main truncate mb-1", children: run.task_id || '（No task ID）' }), _jsxs("div", { className: "text-[10px] text-text-dim flex justify-between", children: [_jsx("span", { children: run.timestamp?.split(' ')[1] || '-' }), run.duration && _jsxs("span", { children: [run.duration.toFixed(1), "s"] })] })] }, run.id)))) }) }) }), _jsx("div", { className: "soft-panel-subtle flex-1 flex flex-col", children: selectedRun ? (_jsx("div", { className: "flex-1 flex flex-col", children: _jsx(ScrollArea, { className: "flex-1 p-6", children: _jsxs("div", { className: "space-y-6", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-3 mb-2", children: [_jsx("h2", { className: "text-2xl font-mono font-semibold text-text-main", children: selectedRun.id }), _jsx("div", { className: `px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider border ${selectedRun.status === 'success'
                                                                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                                                                            : selectedRun.status === 'fail'
                                                                                ? 'bg-red-500/10 text-red-400 border-red-500/20'
                                                                                : 'bg-slate-500/10 text-text-muted border-border'}`, children: selectedRun.status === 'success' ? UI_TERMS.states.success : selectedRun.status === 'fail' ? UI_TERMS.states.failed : UI_TERMS.states.unknown })] }), _jsxs("div", { className: "flex items-center gap-4 text-sm text-text-muted", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Clock, { className: "h-4 w-4" }), selectedRun.timestamp] }), selectedRun.duration && (_jsxs("span", { children: ["\u8017\u65F6: ", _jsxs("span", { className: "text-text-main", children: [selectedRun.duration.toFixed(1), "s"] })] }))] })] }), _jsxs("div", { className: "soft-panel rounded-lg p-4", children: [_jsx("h3", { className: "text-sm font-medium text-text-main mb-2", children: "Task" }), _jsx("div", { className: "soft-inset text-sm text-text-main font-mono p-2 rounded", children: selectedRun.task_id || 'Not recorded' }), selectedRun.error_code && (_jsxs("div", { className: "mt-3", children: [_jsx("div", { className: "text-xs text-red-400 font-semibold mb-1", children: "Failure Reason" }), _jsx("div", { className: "text-sm text-red-300", children: selectedRun.error_code })] }))] }), _jsxs("div", { className: "grid grid-cols-2 gap-4", children: [_jsxs("div", { className: "soft-panel rounded-lg p-4", children: [_jsx("div", { className: "text-xs text-text-muted mb-1", children: "\u98CE\u9669\u5206" }), _jsx("div", { className: "text-xl font-semibold text-text-main", children: selectedRun.risk_score ?? '-' })] }), _jsxs("div", { className: "soft-panel rounded-lg p-4", children: [_jsx("div", { className: "text-xs text-text-muted mb-1", children: "\u5DE5\u5177\u8F6E\u6B21" }), _jsx("div", { className: "text-xl font-semibold text-text-main", children: selectedRun.tool_rounds ?? '-' })] }), _jsxs("div", { className: "soft-panel rounded-lg p-4", children: [_jsx("div", { className: "text-xs text-text-muted mb-1", children: "\u8BFB\u53D6\u884C\u6570" }), _jsx("div", { className: "text-xl font-semibold text-text-main", children: selectedRun.total_lines_read ?? '-' })] })] }), _jsx("div", { className: "pt-4", children: _jsxs(Button, { onClick: () => setShowLogs(true), className: "w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent-hover text-white", children: [_jsx(FileText, { className: "h-4 w-4" }), "View Logs & Artifacts"] }) })] }) }) })) : (_jsxs("div", { className: "flex-1 flex items-center justify-center text-text-dim flex-col gap-2", children: [_jsx(ArrowRight, { className: "h-8 w-8 opacity-20" }), _jsx("p", { children: "Select a history item to view details" })] })) })] })] }) }), _jsx(LogsModal, { isOpen: showLogs, onClose: () => setShowLogs(false), runId: selectedRun?.id, banner: `正在调阅历史案卷日志：${selectedRun?.id}` })] }));
}
