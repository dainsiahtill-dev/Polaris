import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * EvidenceViewer - 决策证据包展示组件
 *
 * Phase 1.1: 展示决策关联的 EvidenceBundle，包括代码 diff、测试结果等
 */
import { useEffect, useState } from 'react';
import { FileCode, GitCommit, TestTube, BarChart3, X, ChevronDown, ChevronRight } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/app/components/ui/card';
import { Badge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
export function EvidenceViewer({ decisionId, workspace, onClose }) {
    const [bundle, setBundle] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [expandedFiles, setExpandedFiles] = useState(new Set());
    useEffect(() => {
        const fetchEvidence = async () => {
            try {
                setLoading(true);
                const response = await fetch(`/v2/resident/decisions/${encodeURIComponent(decisionId)}/evidence?workspace=${encodeURIComponent(workspace)}`);
                if (!response.ok) {
                    if (response.status === 404) {
                        setError('该决策暂无关联的证据包');
                    }
                    else {
                        throw new Error(`Failed to fetch evidence: ${response.status}`);
                    }
                    return;
                }
                const data = await response.json();
                setBundle(data.bundle);
                // Auto-expand first file if only one
                if (data.bundle?.change_set?.length === 1) {
                    setExpandedFiles(new Set([data.bundle.change_set[0].path]));
                }
            }
            catch (err) {
                setError(err instanceof Error ? err.message : 'Failed to load evidence');
            }
            finally {
                setLoading(false);
            }
        };
        void fetchEvidence();
    }, [decisionId, workspace]);
    const toggleFile = (path) => {
        setExpandedFiles((prev) => {
            const next = new Set(prev);
            if (next.has(path)) {
                next.delete(path);
            }
            else {
                next.add(path);
            }
            return next;
        });
    };
    if (loading) {
        return (_jsx(Card, { className: "border-slate-800 bg-slate-900", children: _jsx(CardContent, { className: "py-8 text-center text-slate-500", children: _jsx("div", { className: "animate-pulse", children: "\u52A0\u8F7D\u8BC1\u636E\u5305..." }) }) }));
    }
    if (error) {
        return (_jsxs(Card, { className: "border-slate-800 bg-slate-900", children: [_jsxs(CardHeader, { className: "flex flex-row items-center justify-between", children: [_jsx(CardTitle, { className: "text-sm text-slate-400", children: "\u8BC1\u636E\u5305" }), _jsx(Button, { size: "sm", variant: "ghost", onClick: onClose, children: _jsx(X, { className: "size-4" }) })] }), _jsx(CardContent, { className: "py-4 text-center text-sm text-slate-500", children: error })] }));
    }
    if (!bundle)
        return null;
    const totalAdded = bundle.change_set.reduce((sum, c) => sum + c.lines_added, 0);
    const totalDeleted = bundle.change_set.reduce((sum, c) => sum + c.lines_deleted, 0);
    return (_jsxs(Card, { className: "border-slate-800 bg-slate-900", children: [_jsxs(CardHeader, { className: "flex flex-row items-center justify-between pb-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(GitCommit, { className: "size-4 text-cyan-400" }), _jsx(CardTitle, { className: "text-sm font-medium text-slate-200", children: "\u53D8\u66F4\u8BC1\u636E" }), _jsxs(Badge, { variant: "outline", className: "border-slate-700 text-slate-400 text-xs", children: [bundle.change_set.length, " \u6587\u4EF6"] })] }), _jsx(Button, { size: "sm", variant: "ghost", onClick: onClose, className: "text-slate-400 hover:text-white", children: _jsx(X, { className: "size-4" }) })] }), _jsxs(CardContent, { className: "space-y-3", children: [_jsxs("div", { className: "flex items-center gap-4 text-xs", children: [_jsxs("span", { className: "text-emerald-400", children: ["+", totalAdded] }), _jsxs("span", { className: "text-red-400", children: ["-", totalDeleted] }), _jsx("span", { className: "text-slate-500", children: bundle.working_tree_dirty ? '工作区' : `Commit ${bundle.base_sha.slice(0, 7)}` })] }), _jsx("div", { className: "space-y-1", children: bundle.change_set.map((change) => (_jsx(FileChangeItem, { change: change, expanded: expandedFiles.has(change.path), onToggle: () => toggleFile(change.path) }, change.path))) }), bundle.test_results && (_jsx(TestResultsView, { results: bundle.test_results })), bundle.performance_snapshot && Object.keys(bundle.performance_snapshot.metrics).length > 0 && (_jsx(PerformanceView, { metrics: bundle.performance_snapshot.metrics }))] })] }));
}
function FileChangeItem({ change, expanded, onToggle, }) {
    const changeTypeColors = {
        added: 'text-emerald-400',
        modified: 'text-amber-400',
        deleted: 'text-red-400',
        renamed: 'text-blue-400',
    };
    const changeTypeLabels = {
        added: '新增',
        modified: '修改',
        deleted: '删除',
        renamed: '重命名',
    };
    return (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950", children: [_jsxs("button", { onClick: onToggle, className: "flex w-full items-center justify-between px-3 py-2 text-left hover:bg-slate-900", children: [_jsxs("div", { className: "flex items-center gap-2", children: [expanded ? (_jsx(ChevronDown, { className: "size-4 text-slate-500" })) : (_jsx(ChevronRight, { className: "size-4 text-slate-500" })), _jsx(FileCode, { className: "size-4 text-slate-400" }), _jsx("span", { className: "text-sm text-slate-300", children: change.path }), _jsx(Badge, { variant: "outline", className: cn('text-xs border-transparent', changeTypeColors[change.change_type]), children: changeTypeLabels[change.change_type] })] }), _jsxs("div", { className: "flex items-center gap-2 text-xs", children: [change.lines_added > 0 && (_jsxs("span", { className: "text-emerald-400", children: ["+", change.lines_added] })), change.lines_deleted > 0 && (_jsxs("span", { className: "text-red-400", children: ["-", change.lines_deleted] }))] })] }), expanded && change.patch && (_jsx("div", { className: "border-t border-slate-800", children: _jsx(DiffView, { patch: change.patch }) }))] }));
}
function DiffView({ patch }) {
    const lines = patch.split('\n');
    return (_jsx("div", { className: "max-h-64 overflow-auto p-3 text-xs", children: _jsx("pre", { className: "font-mono leading-relaxed", children: lines.map((line, i) => {
                let lineClass = 'text-slate-300';
                if (line.startsWith('+') && !line.startsWith('+++')) {
                    lineClass = 'bg-emerald-500/10 text-emerald-300';
                }
                else if (line.startsWith('-') && !line.startsWith('---')) {
                    lineClass = 'bg-red-500/10 text-red-300';
                }
                else if (line.startsWith('@@')) {
                    lineClass = 'text-cyan-400';
                }
                else if (line.startsWith('diff') || line.startsWith('index') || line.startsWith('---') || line.startsWith('+++')) {
                    lineClass = 'text-slate-500';
                }
                return (_jsx("div", { className: lineClass, children: line || ' ' }, i));
            }) }) }));
}
function TestResultsView({ results, }) {
    if (!results)
        return null;
    const isSuccess = results.exit_code === 0 && results.failed === 0;
    return (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx(TestTube, { className: cn('size-4', isSuccess ? 'text-emerald-400' : 'text-red-400') }), _jsx("span", { className: "text-sm font-medium text-slate-200", children: "\u6D4B\u8BD5\u7ED3\u679C" }), _jsx(Badge, { variant: "outline", className: cn('text-xs border-transparent', isSuccess ? 'text-emerald-400' : 'text-red-400'), children: isSuccess ? '通过' : '失败' })] }), _jsxs("div", { className: "flex gap-4 text-xs text-slate-400", children: [_jsxs("span", { children: ["\u603B\u8BA1: ", results.total_tests] }), _jsxs("span", { className: "text-emerald-400", children: ["\u901A\u8FC7: ", results.passed] }), results.failed > 0 && _jsxs("span", { className: "text-red-400", children: ["\u5931\u8D25: ", results.failed] }), (results.skipped ?? 0) > 0 && _jsxs("span", { className: "text-amber-400", children: ["\u8DF3\u8FC7: ", results.skipped] })] })] }));
}
function PerformanceView({ metrics }) {
    return (_jsxs("div", { className: "rounded border border-slate-800 bg-slate-950 p-3", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx(BarChart3, { className: "size-4 text-cyan-400" }), _jsx("span", { className: "text-sm font-medium text-slate-200", children: "\u6027\u80FD\u6307\u6807" })] }), _jsx("div", { className: "grid grid-cols-2 gap-2", children: Object.entries(metrics).map(([key, value]) => (_jsxs("div", { className: "flex justify-between text-xs", children: [_jsx("span", { className: "text-slate-500", children: key }), _jsx("span", { className: "text-slate-300", children: value.toFixed(2) })] }, key))) })] }));
}
export default EvidenceViewer;
