import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, Database, Settings, FileText, RefreshCw, ChevronDown, ChevronRight, BarChart3, Brain, ClipboardList, Coins, Trash2, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { clearRoleKernelCache, getRoleKernelCacheStats, getRoleKernelLLMEvents, getRoleKernelTokenBudgetStats, getPmManagementHealth, getPmManagementStatus, getPmStartupDiagnostics, initializePmManagement, } from '@/services/pmService';
function EndpointChip({ endpoint, method, testId, }) {
    return (_jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/60 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpoint, "data-testid": testId, "data-endpoint": endpoint, children: method ? `${method} API` : 'API' }));
}
function evidenceEndpoint(endpoint, workspace = '') {
    const value = String(workspace || '').trim();
    if (!value)
        return endpoint;
    const separator = endpoint.includes('?') ? '&' : '?';
    return `${endpoint}${separator}workspace=${encodeURIComponent(value)}`;
}
function readRecord(value) {
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
}
function readText(record, key) {
    return String(record[key] || '').trim();
}
function llmRoleEvidenceRows(llm) {
    const details = readRecord(llm?.details);
    const roles = readRecord(details?.roles);
    if (!roles)
        return [];
    const roleOrder = [
        ...(llm?.required_ready_roles || []),
        ...(llm?.blocked_roles || []),
        ...Object.keys(roles),
    ];
    const seen = new Set();
    return roleOrder
        .map((rawRole) => String(rawRole || '').trim().toLowerCase())
        .filter((role) => {
        if (!role || seen.has(role))
            return false;
        seen.add(role);
        return true;
    })
        .map((role) => {
        const row = readRecord(roles[role]) || {};
        return {
            role,
            ready: Boolean(row.ready),
            source: readText(row, 'readiness_source') || 'unknown',
            issue: readText(row, 'readiness_issue') || 'ok',
            testedModel: readText(row, 'tested_model') || readText(row, 'model') || 'unknown',
            providerId: readText(row, 'provider_id') || readText(row, 'tested_provider_id') || 'unknown',
        };
    });
}
export function PMDiagnosticsPanel({ isOpen, onClose, workspace = '' }) {
    const [status, setStatus] = useState({
        lancedb: null,
        llm: null,
        workspace: null,
        planningInput: null,
    });
    const [kernelStatus, setKernelStatus] = useState({
        cache: null,
        llmEvents: null,
        tokenBudget: null,
    });
    const [managementStatus, setManagementStatus] = useState({
        status: null,
        health: null,
        initResult: null,
    });
    const [loading, setLoading] = useState(false);
    const [kernelLoading, setKernelLoading] = useState(false);
    const [managementLoading, setManagementLoading] = useState(false);
    const [cacheClearing, setCacheClearing] = useState(false);
    const [managementInitializing, setManagementInitializing] = useState(false);
    const [error, setError] = useState('');
    const [kernelError, setKernelError] = useState('');
    const [managementError, setManagementError] = useState('');
    const [initProjectName, setInitProjectName] = useState('');
    const [initDescription, setInitDescription] = useState('');
    const [expanded, setExpanded] = useState(['all']);
    const loadKernelDiagnostics = useCallback(async () => {
        setKernelLoading(true);
        setKernelError('');
        const errors = [];
        try {
            const [cacheResult, tokenResult, llmResult] = await Promise.all([
                getRoleKernelCacheStats('pm'),
                getRoleKernelTokenBudgetStats('pm'),
                getRoleKernelLLMEvents('pm', { limit: 5, workspace }),
            ]);
            setKernelStatus({
                cache: cacheResult.ok && cacheResult.data ? cacheResult.data : null,
                llmEvents: llmResult.ok && llmResult.data ? llmResult.data : null,
                tokenBudget: tokenResult.ok && tokenResult.data ? tokenResult.data : null,
            });
            if (!cacheResult.ok) {
                errors.push(cacheResult.error || 'PM LLM 缓存统计读取失败');
            }
            if (!tokenResult.ok) {
                errors.push(tokenResult.error || 'PM Token 预算统计读取失败');
            }
            if (!llmResult.ok) {
                errors.push(llmResult.error || 'PM LLM 事件读取失败');
            }
        }
        catch (err) {
            errors.push(err instanceof Error ? err.message : 'PM Kernel 诊断读取失败');
            setKernelStatus({ cache: null, llmEvents: null, tokenBudget: null });
        }
        finally {
            setKernelLoading(false);
        }
        setKernelError(errors.join('；'));
    }, [workspace]);
    const loadManagementDiagnostics = useCallback(async () => {
        setManagementLoading(true);
        setManagementError('');
        try {
            const statusResult = await getPmManagementStatus(workspace);
            if (!statusResult.ok || !statusResult.data) {
                setManagementStatus((current) => ({
                    ...current,
                    status: null,
                    health: null,
                }));
                setManagementError(statusResult.error || 'PM 管理状态读取失败');
                return;
            }
            const nextStatus = statusResult.data;
            if (!nextStatus.initialized) {
                setManagementStatus((current) => ({
                    ...current,
                    status: nextStatus,
                    health: null,
                }));
                return;
            }
            const healthResult = await getPmManagementHealth(workspace);
            setManagementStatus((current) => ({
                ...current,
                status: nextStatus,
                health: healthResult.ok && healthResult.data ? healthResult.data : null,
            }));
            if (!healthResult.ok) {
                setManagementError(healthResult.error || 'PM 项目健康读取失败');
            }
        }
        catch (err) {
            setManagementStatus((current) => ({
                ...current,
                status: null,
                health: null,
            }));
            setManagementError(err instanceof Error ? err.message : 'PM 管理诊断读取失败');
        }
        finally {
            setManagementLoading(false);
        }
    }, [workspace]);
    const runDiagnostics = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const [result] = await Promise.all([
                getPmStartupDiagnostics(workspace),
                loadKernelDiagnostics(),
                loadManagementDiagnostics(),
            ]);
            if (result.ok && result.data) {
                setStatus({
                    lancedb: result.data.lancedb,
                    llm: result.data.llm,
                    workspace: result.data.workspace,
                    planningInput: result.data.planning_input || null,
                });
            }
            else {
                setError(result.error || 'PM 启动诊断读取失败');
            }
        }
        catch (err) {
            setError(err instanceof Error ? err.message : 'PM 启动诊断读取失败');
        }
        finally {
            setLoading(false);
        }
    }, [loadKernelDiagnostics, loadManagementDiagnostics, workspace]);
    const handleClearKernelCache = useCallback(async () => {
        setCacheClearing(true);
        setKernelError('');
        try {
            const result = await clearRoleKernelCache('pm');
            if (result.ok) {
                await loadKernelDiagnostics();
            }
            else {
                setKernelError(result.error || 'PM LLM 缓存清理失败');
            }
        }
        catch (err) {
            setKernelError(err instanceof Error ? err.message : 'PM LLM 缓存清理失败');
        }
        finally {
            setCacheClearing(false);
        }
    }, [loadKernelDiagnostics]);
    const handleInitializeManagement = useCallback(async () => {
        setManagementInitializing(true);
        setManagementError('');
        try {
            const result = await initializePmManagement({
                projectName: initProjectName.trim(),
                description: initDescription.trim(),
            }, workspace);
            if (result.ok && result.data) {
                setManagementStatus((current) => ({
                    ...current,
                    initResult: result.data ?? null,
                }));
                await loadManagementDiagnostics();
            }
            else {
                setManagementError(result.error || 'PM 管理初始化失败');
            }
        }
        catch (err) {
            setManagementError(err instanceof Error ? err.message : 'PM 管理初始化失败');
        }
        finally {
            setManagementInitializing(false);
        }
    }, [initDescription, initProjectName, loadManagementDiagnostics, workspace]);
    useEffect(() => {
        if (isOpen) {
            void runDiagnostics();
        }
    }, [isOpen, runDiagnostics]);
    if (!isOpen)
        return null;
    const allReady = status.lancedb?.ok &&
        status.llm?.state === 'ready' &&
        status.workspace?.status === 'ok' &&
        status.workspace.docs_present &&
        status.planningInput?.ok;
    const roleEvidenceRows = llmRoleEvidenceRows(status.llm);
    const kernelDiagnosticStatus = kernelError
        ? 'error'
        : kernelStatus.cache || kernelStatus.tokenBudget
            ? 'success'
            : 'warning';
    const managementDiagnosticStatus = managementError
        ? 'error'
        : managementStatus.status?.initialized && managementStatus.health
            ? pmManagementHealthTone(managementStatus.health.overall)
            : 'warning';
    return (_jsx("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm", children: _jsxs("div", { className: "w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-amber-500/20 bg-slate-900 shadow-2xl", children: [_jsxs("div", { className: "flex items-center justify-between px-6 py-4 border-b border-white/10", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center", children: _jsx(Settings, { className: "w-4 h-4 text-amber-400" }) }), _jsxs("div", { children: [_jsx("h2", { className: "text-lg font-semibold text-slate-100", children: "PM \u542F\u52A8\u8BCA\u65AD" }), _jsx("p", { className: "text-xs text-slate-500", children: "\u68C0\u67E5\u542F\u52A8\u5931\u8D25\u7684\u5E38\u89C1\u539F\u56E0" })] })] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: onClose, className: "text-slate-400 hover:text-slate-200", children: "\u5173\u95ED" })] }), _jsx("div", { className: "flex-1 overflow-auto p-6 space-y-4", children: loading ? (_jsxs("div", { className: "flex items-center justify-center py-12", children: [_jsx(Loader2, { className: "w-6 h-6 text-amber-400 animate-spin mr-3" }), _jsx("span", { className: "text-slate-400", children: "\u6B63\u5728\u68C0\u67E5..." })] })) : (_jsxs(_Fragment, { children: [error && (_jsx("div", { className: "rounded-lg border border-red-500/25 bg-red-500/10 px-3 py-2 text-sm text-red-200", "data-testid": "pm-diagnostics-error", children: error })), _jsx("div", { className: cn('p-4 rounded-lg border', allReady
                                    ? 'bg-emerald-500/10 border-emerald-500/20'
                                    : 'bg-red-500/10 border-red-500/20'), children: _jsxs("div", { className: "flex items-center gap-3", children: [allReady ? (_jsx(CheckCircle2, { className: "w-5 h-5 text-emerald-400" })) : (_jsx(AlertCircle, { className: "w-5 h-5 text-red-400" })), _jsxs("div", { children: [_jsx("p", { className: cn('font-medium', allReady ? 'text-emerald-400' : 'text-red-400'), children: allReady ? '所有检查通过' : '检测到问题' }), _jsx("p", { className: "text-sm text-slate-400", children: allReady
                                                        ? 'PM 应该可以正常启动'
                                                        : '请解决以下问题后再尝试启动 PM' })] })] }) }), _jsx(DiagnosticItem, { title: "LanceDB \u5411\u91CF\u6570\u636E\u5E93", icon: _jsx(Database, { className: "w-4 h-4" }), status: status.lancedb?.ok ? 'success' : 'error', expanded: expanded.includes('lancedb'), onToggle: () => toggleExpanded('lancedb', expanded, setExpanded), children: status.lancedb?.ok ? (_jsx("p", { className: "text-sm text-slate-300", children: "LanceDB \u6B63\u5E38\u8FD0\u884C" })) : (_jsxs("div", { className: "space-y-2", children: [_jsxs("p", { className: "text-sm text-red-400", children: ["\u9519\u8BEF: ", status.lancedb?.error || 'LanceDB 未就绪'] }), _jsxs("div", { className: "text-sm text-slate-400 space-y-1", children: [_jsx("p", { children: "\u89E3\u51B3\u65B9\u6848:" }), _jsxs("ul", { className: "list-disc list-inside ml-2 space-y-1", children: [_jsx("li", { children: "\u786E\u4FDD LanceDB \u5DF2\u5B89\u88C5: pip install lancedb" }), _jsx("li", { children: "\u68C0\u67E5 Python \u73AF\u5883\u662F\u5426\u6B63\u786E" }), _jsx("li", { children: "\u91CD\u542F\u540E\u7AEF\u670D\u52A1" })] })] })] })) }), _jsx(DiagnosticItem, { title: "LLM \u914D\u7F6E", icon: _jsx(Settings, { className: "w-4 h-4" }), status: status.llm?.state === 'ready'
                                    ? 'success'
                                    : status.llm?.state === 'blocked'
                                        ? 'error'
                                        : 'warning', expanded: expanded.includes('llm'), onToggle: () => toggleExpanded('llm', expanded, setExpanded), children: status.llm?.state === 'ready' ? (_jsxs("div", { className: "space-y-2", children: [_jsx("p", { className: "text-sm text-slate-300", children: "LLM \u914D\u7F6E\u6B63\u5E38" }), roleEvidenceRows.length > 0 && (_jsx("div", { className: "space-y-1", "data-testid": "pm-llm-role-evidence", children: roleEvidenceRows.map((row) => (_jsxs("div", { className: "rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-100", children: [row.role, ": ready \u00B7 ", row.source, " \u00B7 ", row.providerId, " \u00B7 ", row.testedModel] }, row.role))) }))] })) : (_jsxs("div", { className: "space-y-2", children: [_jsxs("p", { className: "text-sm text-red-400", children: ["\u72B6\u6001: ", status.llm?.state || '未知'] }), status.llm?.blocked_roles && status.llm.blocked_roles.length > 0 && (_jsxs("p", { className: "text-sm text-slate-400", children: ["\u963B\u585E\u7684\u89D2\u8272: ", status.llm.blocked_roles.join(', ')] })), roleEvidenceRows.length > 0 && (_jsx("div", { className: "space-y-1", "data-testid": "pm-llm-role-evidence", children: roleEvidenceRows.map((row) => (_jsxs("div", { className: "rounded-md border border-red-500/20 bg-red-500/10 px-2 py-1 text-xs text-red-100", children: [row.role, ": ", row.issue, " \u00B7 ", row.source, " \u00B7 ", row.providerId, " \u00B7 ", row.testedModel] }, row.role))) })), _jsxs("div", { className: "text-sm text-slate-400 space-y-1", children: [_jsx("p", { children: "\u89E3\u51B3\u65B9\u6848:" }), _jsxs("ol", { className: "list-decimal list-inside ml-2 space-y-1", children: [_jsx("li", { children: "\u6253\u5F00\u8BBE\u7F6E (Settings)" }), _jsx("li", { children: "\u8FDB\u5165 LLM \u8BBE\u7F6E\u6807\u7B7E" }), _jsx("li", { children: "\u914D\u7F6E PM \u89D2\u8272\u7684 Provider \u548C Model" }), _jsx("li", { children: "\u8FD0\u884C LLM \u6D4B\u8BD5\u786E\u4FDD\u914D\u7F6E\u6B63\u786E" })] })] })] })) }), _jsx(DiagnosticItem, { title: "\u5DE5\u4F5C\u533A", icon: _jsx(FileText, { className: "w-4 h-4" }), status: status.workspace?.status === 'ok' && status.workspace.docs_present ? 'success' : 'error', expanded: expanded.includes('workspace'), onToggle: () => toggleExpanded('workspace', expanded, setExpanded), children: status.workspace?.status === 'ok' ? (_jsxs("div", { className: "space-y-1", children: [_jsx("p", { className: "text-sm text-slate-300", children: "\u5DE5\u4F5C\u533A\u5DF2\u914D\u7F6E" }), !status.workspace.docs_present && (_jsxs("div", { className: "space-y-2 text-sm", children: [_jsx("p", { className: "text-red-300", children: "docs/ \u76EE\u5F55\u4E0D\u5B58\u5728\uFF0CPM \u542F\u52A8\u5DF2\u88AB\u963B\u65AD" }), _jsxs("div", { className: "text-slate-400 space-y-1", children: [_jsx("p", { children: "\u89E3\u51B3\u65B9\u6848:" }), _jsxs("ul", { className: "list-disc list-inside ml-2 space-y-1", children: [_jsx("li", { children: "\u8FD4\u56DE\u4E3B\u754C\u9762\u5B8C\u6210 docs \u521D\u59CB\u5316" }), _jsx("li", { children: "\u786E\u8BA4\u5DE5\u4F5C\u533A\u5305\u542B\u53EF\u5BA1\u8BA1\u7684 docs/ \u89C4\u5212\u6750\u6599" })] })] })] }))] })) : (_jsxs("div", { className: "space-y-2", children: [_jsx("p", { className: "text-sm text-red-400", children: "\u5DE5\u4F5C\u533A\u672A\u8BBE\u7F6E" }), _jsxs("div", { className: "text-sm text-slate-400 space-y-1", children: [_jsx("p", { children: "\u89E3\u51B3\u65B9\u6848:" }), _jsxs("ul", { className: "list-disc list-inside ml-2 space-y-1", children: [_jsx("li", { children: "\u5728\u4E3B\u754C\u9762\u9009\u62E9\u5DE5\u4F5C\u533A\u76EE\u5F55" }), _jsx("li", { children: "\u786E\u4FDD\u6709\u5199\u5165\u6743\u9650" })] })] })] })) }), _jsx(DiagnosticItem, { title: "\u89C4\u5212\u8F93\u5165", icon: _jsx(ClipboardList, { className: "w-4 h-4" }), status: status.planningInput?.ok ? 'success' : 'error', expanded: expanded.includes('planning-input'), onToggle: () => toggleExpanded('planning-input', expanded, setExpanded), children: status.planningInput?.ok ? (_jsxs("div", { className: "space-y-2", "data-testid": "pm-planning-input-diagnostics", children: [_jsx("p", { className: "text-sm text-slate-300", children: "PM \u5DF2\u627E\u5230\u53EF\u89C4\u5212\u8F93\u5165" }), _jsxs("div", { className: "grid gap-2 rounded-md border border-emerald-500/[0.15] bg-emerald-500/10 p-3 text-xs text-emerald-50", children: [_jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "text-emerald-200/80", children: "\u6765\u6E90" }), _jsx("span", { className: "font-mono", children: formatPlanningInputSource(status.planningInput.source) })] }), _jsxs("div", { className: "flex items-center justify-between gap-2", children: [_jsx("span", { className: "text-emerald-200/80", children: "\u5B57\u7B26/\u5B57\u8282" }), _jsxs("span", { className: "font-mono", children: [formatNumber(status.planningInput.chars), " / ", formatNumber(status.planningInput.bytes)] })] }), _jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "text-emerald-200/80", children: "\u8DEF\u5F84" }), _jsx("div", { className: "truncate font-mono text-[11px]", title: status.planningInput.path || '', children: status.planningInput.path || '-' })] })] })] })) : (_jsxs("div", { className: "space-y-3", "data-testid": "pm-planning-input-diagnostics", children: [_jsx("p", { className: "text-sm text-red-300", children: status.planningInput?.status === 'empty'
                                                ? '规划输入文件为空，PM 启动已被阻断'
                                                : status.planningInput?.status === 'unreadable'
                                                    ? '规划输入无法读取，PM 启动已被阻断'
                                                    : '未找到需求或计划输入，PM 启动已被阻断' }), _jsxs("div", { className: "text-sm text-slate-400 space-y-1", children: [_jsx("p", { children: "\u89E3\u51B3\u65B9\u6848:" }), _jsxs("ul", { className: "list-disc list-inside ml-2 space-y-1", children: [_jsx("li", { children: "\u901A\u8FC7\u653F\u4E8B\u5802\u751F\u6210 docs/product/requirements.md" }), _jsx("li", { children: "\u786E\u8BA4 runtime/contracts/requirements.md \u6216 plan.md \u5DF2\u540C\u6B65" }), _jsx("li", { children: "\u5728 PM Workbench \u4E2D\u8F93\u5165\u660E\u786E directive \u540E\u518D\u8FD0\u884C" })] })] }), (status.planningInput?.checked_paths || []).length > 0 && (_jsx("div", { className: "space-y-1 rounded-md border border-white/10 bg-slate-950/50 p-2 text-[11px] text-slate-400", children: (status.planningInput?.checked_paths || []).slice(0, 5).map((path) => (_jsx("div", { className: "truncate font-mono", title: path, children: path }, path))) })), status.planningInput?.error ? (_jsxs("p", { className: "text-xs text-red-200", children: ["\u9519\u8BEF: ", status.planningInput.error] })) : null] })) }), _jsx(DiagnosticItem, { title: "PM \u7BA1\u7406\u72B6\u6001", icon: _jsx(Settings, { className: "w-4 h-4" }), status: managementDiagnosticStatus, expanded: expanded.includes('management'), onToggle: () => toggleExpanded('management', expanded, setExpanded), children: _jsxs("div", { className: "space-y-3", "data-testid": "pm-management-diagnostics", children: [managementError ? (_jsx("div", { className: "rounded-md border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200", "data-testid": "pm-management-diagnostics-error", children: managementError })) : null, managementLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-sm text-slate-400", children: [_jsx(Loader2, { className: "h-4 w-4 animate-spin text-amber-300" }), "\u6B63\u5728\u8BFB\u53D6 PM \u7BA1\u7406\u72B6\u6001..."] })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "grid gap-3 sm:grid-cols-2", children: [_jsx(ManagementMetricBlock, { label: "\u72B6\u6001", endpoint: evidenceEndpoint('/v2/pm/management/status', workspace), endpointTestId: "pm-management-status-endpoint", rows: [
                                                                ['Initialized', String(managementStatus.status?.initialized ?? false)],
                                                                ['Workspace', managementStatus.status?.workspace || '-'],
                                                                ['Project', readManagementString(managementStatus.status, ['project', 'project_name']) || '-'],
                                                                ['Version', managementStatus.status?.version || '-'],
                                                            ] }), _jsx(ManagementMetricBlock, { label: "\u5065\u5EB7", endpoint: evidenceEndpoint('/v2/pm/management/health', workspace), endpointTestId: "pm-management-health-endpoint", rows: [
                                                                ['Overall', managementStatus.health?.overall || (managementStatus.status?.initialized ? 'unavailable' : 'not initialized')],
                                                                ['Components', String(Object.keys(managementStatus.health?.components || {}).length)],
                                                                ['Metrics', String(Object.keys(managementStatus.health?.metrics || {}).length)],
                                                                ['Advice', String(managementStatus.health?.recommendations?.length || 0)],
                                                            ] })] }), managementStatus.health ? (_jsxs("div", { className: "grid gap-2 rounded-md border border-white/10 bg-white/[0.035] p-3 text-xs text-slate-300", children: [_jsx("div", { className: "flex flex-wrap gap-2", children: Object.entries(managementStatus.health.components).map(([name, value]) => (_jsxs("span", { className: "rounded border border-white/10 bg-slate-950/55 px-2 py-1", children: [name, " \u00B7 ", value] }, name))) }), managementStatus.health.recommendations.length > 0 ? (_jsx("ul", { className: "list-disc space-y-1 pl-4 text-[11px] text-slate-400", children: managementStatus.health.recommendations.slice(0, 4).map((recommendation) => (_jsx("li", { children: recommendation }, recommendation))) })) : null] })) : null, !managementStatus.status?.initialized ? (_jsxs("div", { className: "rounded-md border border-amber-500/20 bg-amber-500/10 p-3", "data-testid": "pm-management-init-panel", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2 text-xs text-amber-100", children: [_jsx("span", { className: "font-medium", children: "PM \u7BA1\u7406\u5C1A\u672A\u521D\u59CB\u5316" }), _jsx(EndpointChip, { endpoint: evidenceEndpoint('/v2/pm/management/init', workspace), method: "POST", testId: "pm-management-init-endpoint" })] }), _jsxs("div", { className: "grid gap-2 sm:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto]", children: [_jsx("input", { value: initProjectName, onChange: (event) => setInitProjectName(event.target.value), placeholder: "Project name", "data-testid": "pm-management-init-project", className: "h-8 rounded-md border border-white/10 bg-slate-950/60 px-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" }), _jsx("input", { value: initDescription, onChange: (event) => setInitDescription(event.target.value), placeholder: "Description", "data-testid": "pm-management-init-description", className: "h-8 rounded-md border border-white/10 bg-slate-950/60 px-2 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-500/50 focus:outline-none" }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void handleInitializeManagement(), disabled: managementInitializing, "data-testid": "pm-management-init-submit", className: "border-amber-500/30 text-amber-100 hover:bg-amber-500/10", children: [managementInitializing ? (_jsx(Loader2, { className: "mr-1.5 h-3.5 w-3.5 animate-spin" })) : (_jsx(CheckCircle2, { className: "mr-1.5 h-3.5 w-3.5" })), "\u521D\u59CB\u5316"] })] })] })) : null, managementStatus.initResult ? (_jsxs("div", { className: "rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100", "data-testid": "pm-management-init-result", children: ["initialized \u00B7 ", managementStatus.initResult.project_name || managementStatus.initResult.message || managementStatus.initResult.workspace] })) : null] }))] }) }), _jsx(DiagnosticItem, { title: "LLM \u7F13\u5B58\u4E0E\u9884\u7B97", icon: _jsx(BarChart3, { className: "w-4 h-4" }), status: kernelDiagnosticStatus, expanded: expanded.includes('kernel'), onToggle: () => toggleExpanded('kernel', expanded, setExpanded), children: _jsxs("div", { className: "space-y-3", "data-testid": "pm-kernel-diagnostics", children: [kernelError && (_jsx("div", { className: "rounded-md border border-red-500/25 bg-red-500/10 px-3 py-2 text-xs text-red-200", "data-testid": "pm-kernel-diagnostics-error", children: kernelError })), kernelLoading ? (_jsxs("div", { className: "flex items-center gap-2 text-sm text-slate-400", children: [_jsx(Loader2, { className: "h-4 w-4 animate-spin text-amber-300" }), "\u6B63\u5728\u8BFB\u53D6 Kernel \u7EDF\u8BA1..."] })) : (_jsxs("div", { className: "grid grid-cols-1 gap-3 sm:grid-cols-3", children: [_jsx(KernelMetricBlock, { icon: _jsx(Database, { className: "h-3.5 w-3.5 text-cyan-300" }), label: "\u7F13\u5B58", endpoint: "/v2/pm/cache-stats", endpointTestId: "pm-kernel-cache-endpoint", rows: [
                                                        ['状态', kernelStatus.cache?.enabled === false ? '关闭' : '开启'],
                                                        ['命中率', formatPercent(kernelStatus.cache?.hit_rate)],
                                                        ['条目', `${formatNumber(kernelStatus.cache?.size)} / ${formatNumber(kernelStatus.cache?.max_size)}`],
                                                        ['命中/未命中', `${formatNumber(kernelStatus.cache?.hits)} / ${formatNumber(kernelStatus.cache?.misses)}`],
                                                    ] }), _jsx(KernelMetricBlock, { icon: _jsx(Coins, { className: "h-3.5 w-3.5 text-emerald-300" }), label: "Token \u9884\u7B97", endpoint: "/v2/pm/token-budget-stats", endpointTestId: "pm-kernel-token-budget-endpoint", rows: [
                                                        ['总量', formatNumber(kernelStatus.tokenBudget?.total)],
                                                        ['对话可用', formatNumber(kernelStatus.tokenBudget?.available_conversation)],
                                                        ['系统/任务', `${formatNumber(kernelStatus.tokenBudget?.system_context)} / ${formatNumber(kernelStatus.tokenBudget?.task_context)}`],
                                                        ['安全边际', formatNumber(kernelStatus.tokenBudget?.safety_margin)],
                                                    ] }), _jsx(KernelMetricBlock, { icon: _jsx(Brain, { className: "h-3.5 w-3.5 text-indigo-300" }), label: "LLM \u4E8B\u4EF6", endpoint: evidenceEndpoint('/v2/pm/llm-events?limit=5', workspace), testId: "pm-llm-events-diagnostics", endpointTestId: "pm-llm-events-endpoint", rows: [
                                                        ['事件数', formatNumber(kernelStatus.llmEvents?.count ?? kernelStatus.llmEvents?.events?.length)],
                                                        ['最近类型', formatKernelEventType(kernelStatus.llmEvents?.events?.[0])],
                                                        ['最近模型', formatKernelEventModel(kernelStatus.llmEvents?.events?.[0])],
                                                        ['错误/重试', `${formatNumber(readStatNumber(kernelStatus.llmEvents?.stats, ['call_error', 'llm_error', 'errors']))} / ${formatNumber(readStatNumber(kernelStatus.llmEvents?.stats, ['call_retry', 'llm_retry', 'retries']))}`],
                                                    ] })] })), _jsxs("div", { className: "flex items-center justify-between gap-3 rounded-md border border-white/10 bg-white/[0.035] px-3 py-2", children: [_jsx("div", { className: "min-w-0 text-xs text-slate-400", children: "\u6E05\u7406\u52A8\u4F5C\u4F1A\u8C03\u7528\u540E\u7AEF\u7F13\u5B58\u7AEF\u70B9\uFF1B\u4E0D\u4F1A\u4FEE\u6539\u5DE5\u4F5C\u533A\u6587\u4EF6\u3002" }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void handleClearKernelCache(), disabled: cacheClearing || kernelLoading, "data-testid": "pm-kernel-cache-clear", className: "shrink-0 border-red-500/25 text-red-200 hover:bg-red-500/10", children: [cacheClearing ? (_jsx(Loader2, { className: "mr-2 h-3.5 w-3.5 animate-spin" })) : (_jsx(Trash2, { className: "mr-2 h-3.5 w-3.5" })), "\u6E05\u7A7A\u7F13\u5B58"] })] })] }) })] })) }), _jsxs("div", { className: "flex items-center justify-between px-6 py-4 border-t border-white/10", children: [_jsxs(Button, { variant: "ghost", size: "sm", onClick: () => void runDiagnostics(), disabled: loading, className: "text-slate-400 hover:text-slate-200", children: [_jsx(RefreshCw, { className: cn('w-4 h-4 mr-2', loading && 'animate-spin') }), "\u91CD\u65B0\u68C0\u67E5"] }), _jsx(Button, { variant: "outline", size: "sm", onClick: onClose, className: "border-white/10 text-slate-300 hover:bg-white/5", children: "\u77E5\u9053\u4E86" })] })] }) }));
}
function formatNumber(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString() : '-';
}
function formatPercent(value) {
    return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}%` : '-';
}
function formatPlanningInputSource(source) {
    const labels = {
        runtime_requirements: 'runtime requirements',
        workspace_requirements: 'workspace requirements',
        legacy_requirements: 'legacy requirements',
        runtime_plan: 'runtime plan',
        workspace_plan: 'workspace plan',
    };
    const token = String(source || '').trim();
    return labels[token] || token || '-';
}
function readManagementString(record, keys) {
    if (!record) {
        return '';
    }
    for (const key of keys) {
        const value = record[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return String(value);
        }
    }
    return '';
}
function pmManagementHealthTone(overall) {
    const token = overall.trim().toLowerCase();
    if (['healthy', 'ok', 'ready', 'pass', 'passed'].includes(token)) {
        return 'success';
    }
    if (['failed', 'error', 'unhealthy', 'blocked'].includes(token)) {
        return 'error';
    }
    return 'warning';
}
function readEventText(event, keys) {
    if (!event) {
        return '';
    }
    for (const key of keys) {
        const value = event[key];
        if (typeof value === 'string' && value.trim()) {
            return value.trim();
        }
        if (typeof value === 'number' && Number.isFinite(value)) {
            return String(value);
        }
    }
    return '';
}
function readStatNumber(stats, keys) {
    if (!stats) {
        return null;
    }
    for (const key of keys) {
        const value = stats[key];
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value;
        }
        if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) {
            return Number(value);
        }
    }
    return null;
}
function formatKernelEventType(event) {
    const eventType = readEventText(event, ['event_type', 'type', 'name']);
    return eventType ? eventType.replace(/_/g, ' ') : '-';
}
function formatKernelEventModel(event) {
    return readEventText(event, ['model', 'provider', 'provider_type']) || '-';
}
function KernelMetricBlock({ icon, label, endpoint, rows, testId, endpointTestId, }) {
    return (_jsxs("div", { className: "min-w-0 rounded-md border border-white/10 bg-white/[0.035] p-3", "data-testid": testId, "data-endpoint": endpoint, children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-xs font-medium text-slate-200", children: [icon, _jsx("span", { className: "truncate", children: label })] }), _jsx(EndpointChip, { endpoint: endpoint, testId: endpointTestId })] }), _jsx("div", { className: "space-y-1", children: rows.map(([name, value]) => (_jsxs("div", { className: "flex items-center justify-between gap-2 text-[11px]", children: [_jsx("span", { className: "text-slate-500", children: name }), _jsx("span", { className: "min-w-0 truncate font-mono text-slate-300", title: value, children: value })] }, name))) })] }));
}
function ManagementMetricBlock({ label, endpoint, rows, endpointTestId, }) {
    return (_jsxs("div", { className: "min-w-0 rounded-md border border-white/10 bg-white/[0.035] p-3", "data-endpoint": endpoint, children: [_jsxs("div", { className: "mb-2 flex items-center justify-between gap-2", children: [_jsx("span", { className: "truncate text-xs font-medium text-slate-200", children: label }), _jsx(EndpointChip, { endpoint: endpoint, testId: endpointTestId })] }), _jsx("div", { className: "space-y-1", children: rows.map(([name, value]) => (_jsxs("div", { className: "flex items-center justify-between gap-2 text-[11px]", children: [_jsx("span", { className: "text-slate-500", children: name }), _jsx("span", { className: "min-w-0 truncate font-mono text-slate-300", title: value, children: value })] }, name))) })] }));
}
function DiagnosticItem({ title, icon, status, expanded, onToggle, children }) {
    const statusColors = {
        success: 'border-emerald-500/20 bg-emerald-500/5',
        warning: 'border-amber-500/20 bg-amber-500/5',
        error: 'border-red-500/20 bg-red-500/5',
    };
    const statusIcons = {
        success: _jsx(CheckCircle2, { className: "w-4 h-4 text-emerald-400" }),
        warning: _jsx(AlertCircle, { className: "w-4 h-4 text-amber-400" }),
        error: _jsx(AlertCircle, { className: "w-4 h-4 text-red-400" }),
    };
    return (_jsxs("div", { className: cn('rounded-lg border', statusColors[status]), children: [_jsxs("button", { onClick: onToggle, className: "w-full flex items-center justify-between p-4 text-left", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "text-slate-400", children: icon }), _jsx("span", { className: "font-medium text-slate-200", children: title })] }), _jsxs("div", { className: "flex items-center gap-2", children: [statusIcons[status], expanded ? (_jsx(ChevronDown, { className: "w-4 h-4 text-slate-500" })) : (_jsx(ChevronRight, { className: "w-4 h-4 text-slate-500" }))] })] }), expanded && _jsx("div", { className: "px-4 pb-4 border-t border-white/5 pt-3", children: children })] }));
}
function toggleExpanded(key, expanded, setExpanded) {
    if (expanded.includes(key)) {
        setExpanded(expanded.filter((k) => k !== key));
    }
    else {
        setExpanded([...expanded, key]);
    }
}
