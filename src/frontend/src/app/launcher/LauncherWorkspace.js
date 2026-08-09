import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, ExternalLink, Info, Loader2, Play, RefreshCw, RotateCcw, Server, Square, TerminalSquare, Trash2, X, } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { StatusBadge } from '@/app/components/ui/badge';
import { useConnectionState, useMessageHandler, useTransportActions, } from '@/runtime/transport';
import { buildInstanceWorkspaceUrl, deleteInstance, getInstanceLogs, listInstances, restartInstance, startInstance, stopInstance, } from '@/services/instances';
export function isLauncherBackendReady(instance) {
    return String(instance.metadata?.backend_health || '').trim() === 'ok';
}
export function isLauncherBackendOpenable(instance) {
    const backendHealth = String(instance.metadata?.backend_health || '').trim();
    const backendOpenable = isLauncherBackendReady(instance) ||
        (instance.status === 'running' && (backendHealth === 'process' || Boolean(instance.backend_alive)));
    if (!backendOpenable)
        return false;
    if (instance.start_frontend === false)
        return true;
    const frontendHealth = String(instance.metadata?.frontend_health || '').trim();
    return frontendHealth === 'ok' || frontendHealth === 'process' || Boolean(instance.frontend_alive);
}
export function launcherInstanceStatusTone(instance) {
    if (instance.status === 'running' && isLauncherBackendOpenable(instance))
        return 'success';
    if (instance.status === 'running')
        return 'warning';
    if (instance.status === 'observed')
        return 'info';
    if (instance.status === 'failed' || instance.status === 'error')
        return 'error';
    if (instance.backend_pid || instance.frontend_pid)
        return 'warning';
    return 'default';
}
export function isLauncherInstanceStoppable(instance) {
    if (instance.status === 'stopped')
        return false;
    if (instance.status === 'running' || instance.status === 'observed')
        return true;
    return Boolean(instance.backend_alive || instance.frontend_alive || instance.backend_pid || instance.frontend_pid);
}
function usesSharedBackendBinding(instance) {
    return String(instance.metadata?.backend_binding || '') === 'shared_backend_workspace_switch';
}
function restartActionLabel(instance) {
    return usesSharedBackendBinding(instance) ? '独立启动' : '重启';
}
function isStoppedInternalBench(instance) {
    return (instance.kind === 'bench_project' &&
        instance.status !== 'running' &&
        !instance.backend_alive &&
        Boolean(instance.metadata?.internal_test_only));
}
function currentControlInstanceId() {
    if (typeof window !== 'undefined') {
        const raw = new URLSearchParams(window.location.search).get('instance');
        if (raw && raw.trim())
            return raw.trim();
    }
    const envInstanceId = import.meta.env.VITE_POLARIS_INSTANCE_ID;
    if (typeof envInstanceId === 'string' && envInstanceId.trim())
        return envInstanceId.trim();
    return 'main';
}
export function isCurrentControlInstance(instance, currentInstanceId = currentControlInstanceId()) {
    return Boolean(currentInstanceId) && instance.instance_id === currentInstanceId;
}
function basename(path) {
    const normalized = String(path || '').replace(/\\/g, '/').replace(/\/+$/, '');
    return normalized.split('/').filter(Boolean).pop() || normalized || 'workspace';
}
function stringField(value) {
    return typeof value === 'string' && value.trim() ? value.trim() : '';
}
function timestampEpoch(value) {
    if (typeof value !== 'string' || !value.trim())
        return 0;
    const epoch = Date.parse(value);
    return Number.isFinite(epoch) ? epoch : 0;
}
function formatTimestampLabel(value) {
    const epoch = timestampEpoch(value);
    if (!epoch)
        return '—';
    const date = new Date(epoch);
    const pad = (part) => String(part).padStart(2, '0');
    return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function launcherInstanceRecencyEpoch(instance) {
    return (timestampEpoch(instance.created_at) ||
        timestampEpoch(instance.last_started_at) ||
        timestampEpoch(instance.updated_at) ||
        timestampEpoch(instance.last_stopped_at));
}
export function sortLauncherInstancesByNewest(instances) {
    return [...instances].sort((left, right) => {
        const timeDelta = launcherInstanceRecencyEpoch(right) - launcherInstanceRecencyEpoch(left);
        if (timeDelta !== 0)
            return timeDelta;
        return right.instance_id.localeCompare(left.instance_id);
    });
}
export function launcherInstanceRecencyLabel(instance) {
    if (timestampEpoch(instance.created_at))
        return `创建 ${formatTimestampLabel(instance.created_at)}`;
    if (timestampEpoch(instance.last_started_at))
        return `启动 ${formatTimestampLabel(instance.last_started_at)}`;
    if (timestampEpoch(instance.updated_at))
        return `更新 ${formatTimestampLabel(instance.updated_at)}`;
    if (timestampEpoch(instance.last_stopped_at))
        return `停止 ${formatTimestampLabel(instance.last_stopped_at)}`;
    return '时间未记录';
}
export function instanceSubtitle(instance) {
    const parts = [instance.instance_id, instance.kind].filter(Boolean);
    if (instance.kind === 'bench_project') {
        const projectId = stringField(instance.bench?.project_id);
        const benchWorkspace = stringField(instance.bench?.bench_workspace);
        if (projectId && !parts.includes(projectId))
            parts.push(projectId);
        if (benchWorkspace)
            parts.push(basename(benchWorkspace));
    }
    return parts.join(' · ');
}
function openInstance(instance) {
    window.open(buildInstanceWorkspaceUrl(instance), '_blank', 'noopener,noreferrer');
}
function formatJson(value) {
    const entries = Object.keys(value || {});
    return entries.length > 0 ? JSON.stringify(value, null, 2) : '{}';
}
const defaultForm = {
    kind: 'project',
    workspace: '',
    name: '',
    backend_reload: false,
    frontend_vite: true,
    start_frontend: true,
};
function isRecord(value) {
    return typeof value === 'object' && value !== null;
}
function isInstanceStatusMessage(message) {
    if (!isRecord(message))
        return false;
    if (String(message.channel || '').trim() === 'status.instances')
        return true;
    if (message.type === 'EVENT' &&
        message.protocol === 'runtime.v2' &&
        isRecord(message.event) &&
        String(message.event.channel || '').trim() === 'status.instances') {
        return true;
    }
    return false;
}
export function LauncherWorkspace() {
    const [instances, setInstances] = useState([]);
    const [form, setForm] = useState(defaultForm);
    const [loading, setLoading] = useState(false);
    const [actionId, setActionId] = useState('');
    const [error, setError] = useState('');
    const [logs, setLogs] = useState(null);
    const [selectedInstanceId, setSelectedInstanceId] = useState('');
    const { subscribeChannels } = useTransportActions();
    const { registerMessageHandler } = useMessageHandler();
    const connection = useConnectionState();
    const runningCount = useMemo(() => instances.filter((item) => item.status === 'running' && isLauncherBackendOpenable(item)).length, [instances]);
    const benchCount = useMemo(() => instances.filter((item) => item.kind === 'bench_project').length, [instances]);
    const stoppedBenchCount = useMemo(() => instances.filter(isStoppedInternalBench).length, [instances]);
    const orderedInstances = useMemo(() => sortLauncherInstancesByNewest(instances), [instances]);
    const selectedInstance = useMemo(() => instances.find((item) => item.instance_id === selectedInstanceId) || null, [instances, selectedInstanceId]);
    const canStart = Boolean(form.workspace?.trim());
    const startDisabled = Boolean(actionId) || !canStart;
    const startTitle = canStart ? '启动新的 Polaris 实例' : '先填写 workspace 路径';
    const refresh = useCallback(async () => {
        setLoading(true);
        setError('');
        const result = await listInstances();
        if (result.ok && result.data) {
            setInstances(result.data.instances);
        }
        else {
            setError(result.error || '实例列表读取失败');
        }
        setLoading(false);
    }, []);
    useEffect(() => {
        void refresh();
    }, [refresh]);
    useEffect(() => {
        const unsubscribe = subscribeChannels([{ channel: 'status.instances', tailLines: 0 }]);
        const unregister = registerMessageHandler((message) => {
            if (isInstanceStatusMessage(message)) {
                void refresh();
            }
        });
        return () => {
            unregister();
            unsubscribe();
        };
    }, [refresh, registerMessageHandler, subscribeChannels]);
    const submitStart = useCallback(async () => {
        if (!form.workspace?.trim()) {
            setError('workspace 不能为空');
            return;
        }
        setActionId('start');
        setError('');
        const result = await startInstance({
            ...form,
            name: form.name?.trim() || basename(form.workspace),
        });
        if (!result.ok) {
            setError(result.error || '启动失败');
        }
        await refresh();
        setActionId('');
    }, [form, refresh]);
    const runAction = useCallback(async (instance, action) => {
        setActionId(`${action}:${instance.instance_id}`);
        setError('');
        if (action === 'stop') {
            const result = await stopInstance(instance.instance_id);
            if (!result.ok)
                setError(result.error || '停止失败');
            await refresh();
        }
        else if (action === 'restart') {
            const result = await restartInstance(instance.instance_id);
            if (!result.ok)
                setError(result.error || '重启失败');
            await refresh();
        }
        else if (action === 'delete') {
            const result = await deleteInstance(instance.instance_id);
            if (!result.ok)
                setError(result.error || '删除失败');
            await refresh();
        }
        else {
            const stream = action === 'frontend-logs' ? 'frontend' : 'backend';
            const result = await getInstanceLogs(instance.instance_id, stream);
            if (result.ok && result.data) {
                setLogs({ instanceId: instance.instance_id, stream, content: result.data.content });
            }
            else {
                setError(result.error || '日志读取失败');
            }
        }
        setActionId('');
    }, [refresh]);
    const cleanupStoppedBench = useCallback(async () => {
        const targets = instances.filter(isStoppedInternalBench);
        if (targets.length === 0)
            return;
        setActionId('cleanup-stopped-bench');
        setError('');
        const failures = [];
        for (const instance of targets) {
            const result = await deleteInstance(instance.instance_id);
            if (!result.ok)
                failures.push(instance.instance_id);
        }
        if (failures.length > 0) {
            setError(`清理失败: ${failures.join(', ')}`);
        }
        await refresh();
        setActionId('');
    }, [instances, refresh]);
    return (_jsxs("div", { className: "flex h-screen min-h-0 flex-col overflow-hidden bg-slate-950 text-slate-100", children: [_jsx("header", { className: "shrink-0 border-b border-cyan-400/20 bg-slate-950/95 px-6 py-4", children: _jsxs("div", { className: "flex flex-wrap items-center justify-between gap-4", children: [_jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-400/30 bg-cyan-400/10 text-cyan-200", children: _jsx(Server, { className: "h-5 w-5" }) }), _jsxs("div", { children: [_jsx("h1", { className: "text-lg font-semibold tracking-tight", children: "Polaris Launcher" }), _jsx("p", { className: "text-xs text-slate-400", children: "\u591A\u5B9E\u4F8B\u603B\u63A7 \u00B7 \u6BCF\u4E2A\u5B9E\u4F8B\u4FDD\u6301\u552F\u4E00 workspace" })] })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsxs(StatusBadge, { color: "success", variant: "dot", children: [runningCount, " running"] }), _jsxs(StatusBadge, { color: "info", variant: "dot", children: [instances.length, " instances"] }), _jsxs(StatusBadge, { color: "warning", variant: "dot", children: [benchCount, " bench"] }), _jsx(StatusBadge, { color: connection.connected ? 'success' : connection.reconnecting ? 'warning' : 'default', variant: "dot", pulse: connection.reconnecting, children: connection.connected ? 'WS live' : connection.reconnecting ? 'WS reconnect' : 'WS idle' }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void refresh(), disabled: loading, children: [loading ? _jsx(Loader2, { className: "h-4 w-4 animate-spin" }) : _jsx(RefreshCw, { className: "h-4 w-4" }), "\u5237\u65B0"] }), stoppedBenchCount > 0 ? (_jsxs(Button, { variant: "outline", size: "sm", onClick: () => void cleanupStoppedBench(), disabled: Boolean(actionId), children: [_jsx(Trash2, { className: "h-4 w-4" }), "\u6E05\u7406\u505C\u6B62\u6D4B\u8BD5(", stoppedBenchCount, ")"] })) : null] })] }) }), _jsxs("main", { className: "grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto p-4 xl:grid-cols-[360px_minmax(0,1fr)]", "data-testid": "launcher-scroll-root", children: [_jsxs("section", { className: "h-fit rounded-lg border border-white/10 bg-white/[0.035] p-4 xl:sticky xl:top-0", children: [_jsx("h2", { className: "text-sm font-semibold text-cyan-100", children: "\u542F\u52A8\u5B9E\u4F8B" }), _jsxs("div", { className: "mt-4 space-y-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-[11px] uppercase text-slate-500", children: "workspace" }), _jsx("input", { value: form.workspace || '', onChange: (event) => {
                                                    setError('');
                                                    setForm((prev) => ({ ...prev, workspace: event.target.value }));
                                                }, placeholder: "/path/to/project", className: "mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-[11px] uppercase text-slate-500", children: "name" }), _jsx("input", { value: form.name || '', onChange: (event) => {
                                                    setError('');
                                                    setForm((prev) => ({ ...prev, name: event.target.value }));
                                                }, placeholder: "\u9ED8\u8BA4\u4F7F\u7528 workspace \u540D\u79F0", className: "mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" })] }), _jsxs("div", { className: "grid grid-cols-2 gap-3", children: [_jsxs("label", { className: "block", children: [_jsx("span", { className: "text-[11px] uppercase text-slate-500", children: "backend port" }), _jsx("input", { value: form.backend_port ?? '', onChange: (event) => setForm((prev) => ({ ...prev, backend_port: event.target.value ? Number(event.target.value) : null })), placeholder: "auto", className: "mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-[11px] uppercase text-slate-500", children: "frontend port" }), _jsx("input", { value: form.frontend_port ?? '', onChange: (event) => setForm((prev) => ({ ...prev, frontend_port: event.target.value ? Number(event.target.value) : null })), placeholder: "auto", className: "mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60" })] })] }), _jsxs("label", { className: "block", children: [_jsx("span", { className: "text-[11px] uppercase text-slate-500", children: "kind" }), _jsxs("select", { value: form.kind || 'project', onChange: (event) => setForm((prev) => ({ ...prev, kind: event.target.value })), className: "mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-400/60", children: [_jsx("option", { value: "project", children: "project" }), _jsx("option", { value: "bench_project", children: "bench_project" }), _jsx("option", { value: "internal_test", children: "internal_test" })] })] }), _jsxs("div", { className: "space-y-2 rounded-md border border-white/10 bg-slate-950/60 p-3", children: [_jsxs("label", { className: "flex items-center justify-between gap-3 text-sm text-slate-300", children: ["backend --reload", _jsx("input", { type: "checkbox", checked: form.backend_reload !== false, onChange: (event) => setForm((prev) => ({ ...prev, backend_reload: event.target.checked })) })] }), _jsxs("label", { className: "flex items-center justify-between gap-3 text-sm text-slate-300", children: ["frontend Vite", _jsx("input", { type: "checkbox", checked: form.start_frontend !== false, onChange: (event) => setForm((prev) => ({ ...prev, start_frontend: event.target.checked, frontend_vite: event.target.checked })) })] })] }), _jsxs(Button, { className: "w-full", onClick: () => void submitStart(), disabled: startDisabled, title: startTitle, "aria-label": "\u542F\u52A8 Polaris \u5B9E\u4F8B", children: [actionId === 'start' ? _jsx(Loader2, { className: "h-4 w-4 animate-spin" }) : _jsx(Play, { className: "h-4 w-4" }), actionId === 'start' ? '正在启动...' : canStart ? '启动' : '填写 workspace 后启动'] })] }), error ? (_jsx("div", { className: "mt-4 rounded-md border border-red-400/30 bg-red-500/10 px-3 py-2 text-sm text-red-200", children: error })) : null] }), _jsxs("section", { className: "flex min-h-[520px] min-w-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-white/[0.035]", "data-testid": "launcher-instance-panel", children: [_jsxs("div", { className: "flex shrink-0 items-center justify-between gap-3 border-b border-white/10 px-4 py-3", children: [_jsxs("div", { children: [_jsx("h2", { className: "text-sm font-semibold text-slate-100", children: "\u5B9E\u4F8B" }), _jsx("p", { className: "mt-0.5 text-[11px] text-slate-500", children: "\u6700\u65B0\u521B\u5EFA/\u542F\u52A8\u4F18\u5148" })] }), _jsxs("span", { className: "text-xs text-slate-500", children: ["\u5171 ", instances.length, " \u4E2A"] })] }), _jsx("div", { className: "min-h-0 flex-1 overflow-y-auto p-4", "data-testid": "launcher-instance-list", children: _jsx("div", { className: "grid gap-3 md:grid-cols-2 2xl:grid-cols-3", children: instances.length === 0 ? (_jsx("div", { className: "col-span-full rounded-lg border border-dashed border-white/10 p-8 text-center text-sm text-slate-500", children: "\u6682\u65E0\u5B9E\u4F8B" })) : orderedInstances.map((instance) => {
                                        const isCurrentControl = isCurrentControlInstance(instance);
                                        const stoppingActionId = `stop:${instance.instance_id}`;
                                        const restartingActionId = `restart:${instance.instance_id}`;
                                        const deletingActionId = `delete:${instance.instance_id}`;
                                        const isStopping = actionId === stoppingActionId;
                                        const isRestarting = actionId === restartingActionId;
                                        const isDeleting = actionId === deletingActionId;
                                        const canStop = isLauncherInstanceStoppable(instance);
                                        const openable = isLauncherBackendOpenable(instance);
                                        const openLabel = openable ? '打开' : instance.status === 'stopped' ? '已停止' : '等待后端';
                                        const openTitle = openable
                                            ? '打开该实例工作台'
                                            : instance.status === 'stopped'
                                                ? '实例已停止，不能打开工作台'
                                                : '后端或前端尚未就绪';
                                        const stopDisabled = Boolean(actionId) || isCurrentControl || !canStop;
                                        const stopTitle = isCurrentControl
                                            ? '当前控制后端不能自我停止'
                                            : canStop
                                                ? '停止该 Polaris 实例'
                                                : '实例已停止，停止操作不可用';
                                        const statusLabel = isStopping ? 'stopping...' : instance.status;
                                        const statusTone = isStopping ? 'warning' : launcherInstanceStatusTone(instance);
                                        return (_jsxs("article", { className: "rounded-lg border border-cyan-300/10 bg-slate-950/80 p-4", children: [_jsx("div", { className: "flex items-start justify-between gap-3", children: _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("h3", { className: "truncate text-sm font-semibold text-slate-100", children: instance.name }), _jsx(StatusBadge, { color: statusTone, variant: "dot", pulse: instance.status === 'running' || isStopping, "data-testid": `launcher-instance-status-${instance.instance_id}`, children: statusLabel })] }), _jsx("p", { className: "mt-1 truncate text-[11px] uppercase text-slate-500", title: instance.workspace, children: instanceSubtitle(instance) })] }) }), _jsxs("dl", { className: "mt-4 grid grid-cols-2 gap-2 text-xs", children: [_jsxs("div", { className: "rounded-md bg-white/[0.04] px-2 py-2", children: [_jsx("dt", { className: "text-[10px] uppercase text-slate-500", children: "backend" }), _jsx("dd", { className: "mt-1 font-mono text-cyan-100", children: instance.backend_port })] }), _jsxs("div", { className: "rounded-md bg-white/[0.04] px-2 py-2", children: [_jsx("dt", { className: "text-[10px] uppercase text-slate-500", children: "frontend" }), _jsx("dd", { className: "mt-1 font-mono text-cyan-100", children: instance.frontend_port })] }), _jsxs("div", { className: "col-span-2 rounded-md bg-white/[0.04] px-2 py-2", children: [_jsx("dt", { className: "text-[10px] uppercase text-slate-500", children: "workspace" }), _jsx("dd", { className: "mt-1 truncate text-slate-300", title: instance.workspace, children: instance.workspace })] }), _jsxs("div", { className: "col-span-2 rounded-md bg-white/[0.04] px-2 py-2", children: [_jsx("dt", { className: "text-[10px] uppercase text-slate-500", children: "recent" }), _jsx("dd", { className: "mt-1 font-mono text-[11px] text-slate-300", children: launcherInstanceRecencyLabel(instance) })] })] }), _jsxs("div", { className: "mt-4 flex flex-wrap gap-2", children: [_jsxs(Button, { size: "sm", onClick: () => openInstance(instance), disabled: !openable, title: openTitle, "aria-label": `打开实例 ${instance.instance_id}`, "data-testid": `launcher-instance-open-${instance.instance_id}`, children: [_jsx(ExternalLink, { className: "h-3.5 w-3.5" }), openLabel] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void runAction(instance, 'restart'), disabled: Boolean(actionId) || isCurrentControl, title: isCurrentControl ? '当前控制后端不能自我重启' : undefined, "aria-label": `${restartActionLabel(instance)}实例 ${instance.instance_id}`, children: [isRestarting ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }) : _jsx(RotateCcw, { className: "h-3.5 w-3.5" }), isRestarting ? '正在重启...' : restartActionLabel(instance)] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void runAction(instance, 'stop'), disabled: stopDisabled, title: stopTitle, "aria-label": `停止实例 ${instance.instance_id}`, "data-testid": `launcher-instance-stop-${instance.instance_id}`, children: [isStopping ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }) : _jsx(Square, { className: "h-3.5 w-3.5" }), isStopping ? '正在停止中...' : '停止'] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void runAction(instance, 'backend-logs'), children: [_jsx(TerminalSquare, { className: "h-3.5 w-3.5" }), "\u540E\u7AEF\u65E5\u5FD7"] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => setSelectedInstanceId(instance.instance_id), children: [_jsx(Info, { className: "h-3.5 w-3.5" }), "\u8BE6\u60C5"] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: () => void runAction(instance, 'delete'), disabled: Boolean(actionId) || isCurrentControl, title: isCurrentControl ? '当前控制后端不能删除自身记录' : '删除实例记录', "aria-label": `删除实例 ${instance.instance_id}`, children: isDeleting ? _jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" }) : _jsx(Trash2, { className: "h-3.5 w-3.5" }) })] })] }, instance.instance_id));
                                    }) }) })] })] }), logs ? (_jsxs("aside", { className: "fixed bottom-4 right-4 z-50 flex max-h-[50vh] w-[min(720px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-cyan-400/20 bg-slate-950 shadow-2xl", children: [_jsxs("div", { className: "flex items-center justify-between border-b border-white/10 px-3 py-2", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm text-cyan-100", children: [_jsx(Activity, { className: "h-4 w-4" }), logs.instanceId, " \u00B7 ", logs.stream] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: () => setLogs(null), children: "\u5173\u95ED" })] }), _jsx("pre", { className: "min-h-0 overflow-auto p-3 text-xs leading-relaxed text-slate-300", children: logs.content || '暂无日志' })] })) : null, selectedInstance ? (_jsxs("aside", { className: "fixed right-4 top-20 z-40 flex max-h-[calc(100vh-6rem)] w-[min(560px,calc(100vw-2rem))] flex-col overflow-hidden rounded-lg border border-cyan-400/20 bg-slate-950 shadow-2xl", children: [_jsxs("div", { className: "flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3", children: [_jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("h2", { className: "truncate text-sm font-semibold text-cyan-100", children: selectedInstance.name }), _jsx(StatusBadge, { color: launcherInstanceStatusTone(selectedInstance), variant: "dot", children: selectedInstance.status })] }), _jsxs("p", { className: "mt-1 truncate text-xs text-slate-500", children: [selectedInstance.instance_id, " \u00B7 ", selectedInstance.kind] })] }), _jsx(Button, { variant: "ghost", size: "sm", onClick: () => setSelectedInstanceId(''), children: _jsx(X, { className: "h-4 w-4" }) })] }), _jsxs("div", { className: "min-h-0 space-y-4 overflow-auto p-4 text-xs", children: [_jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs("div", { className: "rounded-md border border-white/10 bg-white/[0.04] p-3", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "backend health" }), _jsx("div", { className: "mt-1 text-sm font-semibold text-cyan-100", children: selectedInstance.backend_alive ? 'alive' : 'offline' }), _jsx("div", { className: "mt-1 font-mono text-slate-500", children: String(selectedInstance.metadata.backend_health || 'unknown') })] }), _jsxs("div", { className: "rounded-md border border-white/10 bg-white/[0.04] p-3", children: [_jsx("div", { className: "text-[10px] uppercase text-slate-500", children: "frontend health" }), _jsx("div", { className: "mt-1 text-sm font-semibold text-cyan-100", children: selectedInstance.frontend_alive ? 'alive' : 'offline' }), _jsx("div", { className: "mt-1 font-mono text-slate-500", children: String(selectedInstance.metadata.frontend_health || 'unknown') })] })] }), _jsx("dl", { className: "space-y-2", children: [
                                    ['workspace', selectedInstance.workspace],
                                    ['runtime_root', selectedInstance.runtime_root],
                                    ['backend_url', selectedInstance.backend_url],
                                    ['frontend_url', selectedInstance.frontend_url || '(backend-only)'],
                                    ['open_url', buildInstanceWorkspaceUrl(selectedInstance)],
                                ].map(([label, value]) => (_jsxs("div", { className: "rounded-md border border-white/10 bg-slate-900/80 p-3", children: [_jsx("dt", { className: "text-[10px] uppercase text-slate-500", children: label }), _jsx("dd", { className: "mt-1 break-all font-mono text-slate-200", children: value })] }, label))) }), _jsxs("div", { className: "grid grid-cols-2 gap-2", children: [_jsxs(Button, { size: "sm", onClick: () => openInstance(selectedInstance), children: [_jsx(ExternalLink, { className: "h-3.5 w-3.5" }), "\u6253\u5F00\u5B9E\u4F8B"] }), _jsxs(Button, { variant: "outline", size: "sm", onClick: () => void runAction(selectedInstance, 'frontend-logs'), children: [_jsx(TerminalSquare, { className: "h-3.5 w-3.5" }), "\u524D\u7AEF\u65E5\u5FD7"] })] }), _jsxs("section", { children: [_jsx("h3", { className: "text-[11px] font-semibold uppercase text-slate-500", children: "bench metadata" }), _jsx("pre", { className: "mt-2 max-h-40 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300", children: formatJson(selectedInstance.bench) })] }), _jsxs("section", { children: [_jsx("h3", { className: "text-[11px] font-semibold uppercase text-slate-500", children: "instance metadata" }), _jsx("pre", { className: "mt-2 max-h-40 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-[11px] leading-relaxed text-slate-300", children: formatJson(selectedInstance.metadata) })] })] })] })) : null] }));
}
