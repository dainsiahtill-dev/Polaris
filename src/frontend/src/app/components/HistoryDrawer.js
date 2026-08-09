import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Drawer, DrawerContent, DrawerDescription, DrawerTitle } from '@/app/components/ui/drawer';
import { AlertTriangle, CheckCircle, Database, Download, FileWarning, History, KeyRound, RefreshCw, Search, XCircle, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { Input } from '@/app/components/ui/input';
import { ScrollArea } from '@/app/components/ui/scroll-area';
import { Badge } from '@/app/components/ui/badge';
import { getControlPlaneProjection, } from '@/services/controlPlane';
function ledgerStatusView(projection) {
    if (!projection) {
        return {
            label: '等待账本',
            detail: 'Control Plane Run Ledger projection 尚未加载',
            tone: 'hold',
        };
    }
    if (!projection.available) {
        return {
            label: '账本不可用',
            detail: projection.detail || 'Run Ledger projection endpoint unavailable',
            tone: 'fail',
        };
    }
    if (projection.projected <= 0) {
        return {
            label: '等待证据',
            detail: projection.detail || 'Run Ledger 尚无可投影项目',
            tone: 'hold',
        };
    }
    if (projection.ok) {
        return {
            label: '已验证',
            detail: `Run Ledger verified ${projection.projected}/${projection.total || projection.projected}`,
            tone: 'ok',
        };
    }
    if (projection.failed > 0) {
        return {
            label: '门禁失败',
            detail: projection.detail || `${projection.failed} 个账本投影失败`,
            tone: 'fail',
        };
    }
    if (projection.missing > 0) {
        return {
            label: '证据缺失',
            detail: projection.detail || `${projection.missing} 个项目缺少物理证据`,
            tone: 'hold',
        };
    }
    return {
        label: projection.status || '账本待定',
        detail: projection.detail || 'Run Ledger projection is not terminal',
        tone: 'hold',
    };
}
function toneClass(tone) {
    if (tone === 'ok')
        return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
    if (tone === 'fail')
        return 'border-red-500/30 bg-red-500/10 text-red-200';
    return 'border-amber-500/30 bg-amber-500/10 text-amber-200';
}
function projectState(project) {
    if (project.ok) {
        return {
            label: 'PASS',
            detail: project.detail || 'physical evidence verified',
            tone: 'ok',
        };
    }
    if (project.failed_gate_count > 0 || !project.integrity_ok || !project.outcome_ok) {
        return {
            label: 'FAIL',
            detail: project.detail || 'gate or integrity evidence failed',
            tone: 'fail',
        };
    }
    if (project.missing.length > 0) {
        return {
            label: 'HOLD',
            detail: project.missing.join(', '),
            tone: 'hold',
        };
    }
    return {
        label: 'PENDING',
        detail: project.detail || 'waiting for ledger receipts',
        tone: 'hold',
    };
}
function statusIcon(view) {
    if (view.tone === 'ok')
        return _jsx(CheckCircle, { className: "h-4 w-4 text-emerald-300" });
    if (view.tone === 'fail')
        return _jsx(XCircle, { className: "h-4 w-4 text-red-300" });
    return _jsx(AlertTriangle, { className: "h-4 w-4 text-amber-300" });
}
function statusBadge(view) {
    return (_jsx(Badge, { variant: "outline", className: `border ${toneClass(view.tone)}`, children: view.label }));
}
function evidenceSummary(project) {
    const modalities = project.evidence_modalities || {};
    const rows = Object.entries(modalities)
        .map(([name, summary]) => `${name}: ${summary.ok}/${summary.total}`)
        .filter(Boolean);
    if (rows.length > 0)
        return rows.join(' · ');
    if (project.evidence_policy) {
        const required = project.evidence_policy.required_modalities;
        return required.length > 0 ? `required: ${required.join(', ')}` : 'policy: optional evidence';
    }
    return 'evidence policy: not declared';
}
function projectSearchText(project) {
    return [
        project.project_id,
        project.latest_token_id,
        project.detail,
        ...project.missing,
        evidenceSummary(project),
    ]
        .join(' ')
        .toLowerCase();
}
export function RunLedgerHistoryContent({ defaultLimit = 100, workspace }) {
    const [projection, setProjection] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [query, setQuery] = useState('');
    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await getControlPlaneProjection({ workspace, maxRuns: defaultLimit });
            if (!result.ok || !result.data) {
                setProjection(null);
                setError(result.error || 'Run Ledger projection unavailable');
                return;
            }
            setProjection(result.data);
        }
        catch (err) {
            setProjection(null);
            setError(err instanceof Error ? err.message : 'Run Ledger projection unavailable');
        }
        finally {
            setLoading(false);
        }
    }, [defaultLimit, workspace]);
    useEffect(() => {
        load();
    }, [load]);
    const projects = projection?.projects ?? [];
    const filteredProjects = useMemo(() => {
        if (!query.trim())
            return projects;
        const q = query.toLowerCase();
        return projects.filter((project) => projectSearchText(project).includes(q));
    }, [projects, query]);
    const status = ledgerStatusView(projection);
    const exportHistory = () => {
        if (!projection)
            return;
        const payload = JSON.stringify({
            source: 'control_plane_run_ledger_projection',
            workspace: workspace || '',
            projection,
            filtered_projects: filteredProjects,
        }, null, 2);
        const blob = new Blob([payload], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `run-ledger-history-${new Date().toISOString().split('T')[0]}.json`;
        anchor.click();
        URL.revokeObjectURL(url);
    };
    return (_jsxs("div", { className: "flex h-full flex-col bg-[var(--ink-indigo)]", children: [_jsxs("div", { className: "flex items-center justify-between border-b border-gray-800 p-4", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2", children: [_jsx(History, { className: "h-5 w-5 shrink-0 text-cyan-300" }), _jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "text-lg font-semibold text-gray-100", children: "Run Ledger \u6848\u5377" }), _jsxs("div", { className: "truncate text-xs text-gray-500", children: [workspace || 'current workspace', " \u00B7 ", projection?.audit_path || 'waiting for ledger'] })] }), _jsxs(Badge, { variant: "outline", className: "border-cyan-500/30 bg-cyan-500/10 text-xs text-cyan-200", children: [filteredProjects.length, " project"] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Button, { variant: "ghost", size: "sm", onClick: load, disabled: loading, className: "text-gray-400 hover:text-white", children: _jsx(RefreshCw, { className: `h-4 w-4 ${loading ? 'animate-spin' : ''}` }) }), _jsx(Button, { variant: "ghost", size: "sm", onClick: exportHistory, disabled: !projection, className: "text-gray-400 hover:text-white", children: _jsx(Download, { className: "h-4 w-4" }) })] })] }), _jsxs("div", { className: "grid grid-cols-2 gap-2 border-b border-gray-800 p-4 text-xs md:grid-cols-4", children: [_jsxs("div", { className: `rounded border p-3 ${toneClass(status.tone)}`, children: [_jsxs("div", { className: "mb-1 flex items-center gap-2 font-semibold", children: [statusIcon(status), status.label] }), _jsx("div", { className: "text-[11px] opacity-80", children: status.detail })] }), _jsxs("div", { className: "rounded border border-cyan-500/20 bg-cyan-500/5 p-3 text-cyan-100", children: [_jsxs("div", { className: "flex items-center gap-2 text-gray-400", children: [_jsx(Database, { className: "h-3.5 w-3.5" }), "Projection"] }), _jsxs("div", { className: "mt-1 font-mono text-lg", children: [projection?.projected ?? 0, "/", projection?.total ?? 0] })] }), _jsxs("div", { className: "rounded border border-red-500/20 bg-red-500/5 p-3 text-red-100", children: [_jsxs("div", { className: "flex items-center gap-2 text-gray-400", children: [_jsx(FileWarning, { className: "h-3.5 w-3.5" }), "Failed Gates"] }), _jsx("div", { className: "mt-1 font-mono text-lg", children: projection?.failed ?? 0 })] }), _jsxs("div", { className: "rounded border border-amber-500/20 bg-amber-500/5 p-3 text-amber-100", children: [_jsxs("div", { className: "flex items-center gap-2 text-gray-400", children: [_jsx(KeyRound, { className: "h-3.5 w-3.5" }), "Missing Evidence"] }), _jsx("div", { className: "mt-1 font-mono text-lg", children: projection?.missing ?? 0 })] })] }), projection?.compat_ledgers_included ? (_jsx("div", { className: "border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-200", children: "compat ledger included \u00B7 \u5185\u90E8\u6D4B\u8BD5\u8D26\u672C\u53EA\u4F5C\u4E3A\u5E73\u53F0\u6295\u5F71\u8F93\u5165\uFF0C\u4E0D\u662F\u6B63\u5F0F UI \u7684\u4E8B\u5B9E\u6E90\u3002" })) : null, _jsx("div", { className: "border-b border-gray-800 p-4", children: _jsxs("div", { className: "relative", children: [_jsx(Search, { className: "absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" }), _jsx(Input, { placeholder: "\u641C\u7D22 project\u3001job token\u3001\u8BC1\u636E\u7F3A\u53E3...", value: query, onChange: (event) => setQuery(event.target.value), className: "border-gray-700 bg-[#0b1020] pl-10 text-gray-200 placeholder-gray-500" })] }) }), _jsx(ScrollArea, { className: "flex-1", children: _jsx("div", { className: "space-y-3 p-4", children: loading ? (_jsx("div", { className: "py-8 text-center text-gray-500", children: "\u52A0\u8F7D Run Ledger..." })) : error ? (_jsxs("div", { className: "rounded border border-red-500/30 bg-red-500/10 p-4 text-center text-red-200", children: ["\u8D26\u672C\u8BFB\u53D6\u5931\u8D25: ", error] })) : filteredProjects.length === 0 ? (_jsx("div", { className: "rounded border border-gray-700 bg-[#11172a] p-4 text-center text-gray-500", children: "\u6682\u65E0 Run Ledger \u6295\u5F71\u8BB0\u5F55" })) : (filteredProjects.map((project) => {
                        const state = projectState(project);
                        return (_jsxs("article", { className: "rounded-lg border border-gray-700 bg-[#11172a] p-4 shadow-[0_0_32px_rgba(0,255,255,0.05)]", children: [_jsxs("div", { className: "flex items-start justify-between gap-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [statusIcon(state), _jsx("span", { className: "font-mono text-sm text-cyan-200", children: project.project_id }), statusBadge(state)] }), _jsx("div", { className: "mt-2 break-words text-sm text-gray-300", children: state.detail })] }), _jsxs("div", { className: "shrink-0 rounded border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-right text-xs text-cyan-100", children: [_jsx("div", { className: "text-gray-500", children: "gates" }), _jsxs("div", { className: "font-mono", children: [project.gate_count - project.failed_gate_count, "/", project.gate_count] })] })] }), _jsxs("div", { className: "mt-3 grid gap-2 text-xs md:grid-cols-2", children: [_jsxs("div", { className: "rounded border border-gray-700 bg-[#0b1020] p-2 text-gray-300", children: [_jsx("div", { className: "text-gray-500", children: "latest job token" }), _jsx("div", { className: "mt-1 break-all font-mono text-cyan-200", children: project.latest_token_id || 'n/a' })] }), _jsxs("div", { className: "rounded border border-gray-700 bg-[#0b1020] p-2 text-gray-300", children: [_jsx("div", { className: "text-gray-500", children: "evidence" }), _jsx("div", { className: "mt-1 break-words text-gray-200", children: evidenceSummary(project) })] })] }), project.missing.length > 0 ? (_jsxs("div", { className: "mt-3 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-200", children: [_jsx("div", { className: "font-semibold", children: "missing evidence" }), _jsx("div", { className: "mt-1 break-words", children: project.missing.join(', ') })] })) : null] }, `${project.project_id}-${project.latest_token_id}`));
                    })) }) })] }));
}
export function HistoryDrawer({ open, onOpenChange, defaultLimit, workspace }) {
    return (_jsx(Drawer, { open: open, onOpenChange: onOpenChange, direction: "right", children: _jsxs(DrawerContent, { "data-testid": "history-drawer", className: "left-auto right-0 top-0 bottom-0 h-dvh border-l border-gray-800 bg-[var(--ink-indigo)] data-[state=open]:!translate-x-0 data-[state=open]:!transform-none", style: {
                backgroundColor: 'rgb(18, 14, 42)',
                boxSizing: 'border-box',
                right: 0,
                width: 'min(42rem, calc(100vw - 2rem))',
                maxWidth: 'calc(100vw - 2rem)',
            }, children: [_jsx(DrawerTitle, { className: "sr-only", children: "Run Ledger \u6848\u5377" }), _jsx(DrawerDescription, { className: "sr-only", children: "\u67E5\u770B Control Plane Run Ledger \u6295\u5F71\u3001\u7269\u7406\u8BC1\u636E\u3001\u95E8\u7981\u7ED3\u679C\u548C\u7F3A\u5931\u9879\u3002" }), _jsx("div", { className: "flex-1 overflow-hidden", children: _jsx(RunLedgerHistoryContent, { defaultLimit: defaultLimit, workspace: workspace }) })] }) }));
}
