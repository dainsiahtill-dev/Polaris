import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, GitCompare, Loader2, RefreshCw, Settings2, SlidersHorizontal } from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import { settingsService } from '@/services';
import { StrategyDiffViewer } from './StrategyDiffViewer';
import { StrategyEditorPanel } from './StrategyEditorPanel';
const DIRECTOR_STRATEGY_DEFAULTS = {
    name: 'director-runtime',
    version: '1.0.0',
    mode: 'parallel',
    limits: {
        iterations: 1,
        maxParallelTasks: 3,
        readyTimeoutSeconds: 30,
        claimTimeoutSeconds: 30,
        phaseTimeoutSeconds: 900,
        completeTimeoutSeconds: 30,
        taskTimeoutSeconds: 3600,
    },
    observability: {
        forever: false,
        showOutput: true,
    },
    metadata: {
        source: 'polaris-settings',
    },
};
export function buildDirectorStrategyFromSettings(settings, workspace) {
    const mode = normalizeMode(settings?.director_execution_mode);
    return {
        ...DIRECTOR_STRATEGY_DEFAULTS,
        mode,
        limits: {
            iterations: readPositiveInteger(settings?.director_iterations, DIRECTOR_STRATEGY_DEFAULTS.limits.iterations),
            maxParallelTasks: readPositiveInteger(settings?.director_max_parallel_tasks, DIRECTOR_STRATEGY_DEFAULTS.limits.maxParallelTasks),
            readyTimeoutSeconds: readPositiveInteger(settings?.director_ready_timeout_seconds, DIRECTOR_STRATEGY_DEFAULTS.limits.readyTimeoutSeconds),
            claimTimeoutSeconds: readPositiveInteger(settings?.director_claim_timeout_seconds, DIRECTOR_STRATEGY_DEFAULTS.limits.claimTimeoutSeconds),
            phaseTimeoutSeconds: readPositiveInteger(settings?.director_phase_timeout_seconds, DIRECTOR_STRATEGY_DEFAULTS.limits.phaseTimeoutSeconds),
            completeTimeoutSeconds: readPositiveInteger(settings?.director_complete_timeout_seconds, DIRECTOR_STRATEGY_DEFAULTS.limits.completeTimeoutSeconds),
            taskTimeoutSeconds: readPositiveInteger(settings?.director_task_timeout_seconds, DIRECTOR_STRATEGY_DEFAULTS.limits.taskTimeoutSeconds),
        },
        observability: {
            forever: Boolean(settings?.director_forever ?? DIRECTOR_STRATEGY_DEFAULTS.observability.forever),
            showOutput: Boolean(settings?.director_show_output ?? DIRECTOR_STRATEGY_DEFAULTS.observability.showOutput),
        },
        metadata: {
            source: '/v2/settings',
            workspace: settings?.workspace || workspace,
            updatedAt: new Date().toISOString(),
        },
    };
}
export function buildDirectorSettingsUpdateFromStrategy(strategy) {
    return {
        director_execution_mode: normalizeMode(strategy.mode),
        director_iterations: readPositiveInteger(strategy.limits.iterations, DIRECTOR_STRATEGY_DEFAULTS.limits.iterations),
        director_max_parallel_tasks: readPositiveInteger(strategy.limits.maxParallelTasks, DIRECTOR_STRATEGY_DEFAULTS.limits.maxParallelTasks),
        director_ready_timeout_seconds: readPositiveInteger(strategy.limits.readyTimeoutSeconds, DIRECTOR_STRATEGY_DEFAULTS.limits.readyTimeoutSeconds),
        director_claim_timeout_seconds: readPositiveInteger(strategy.limits.claimTimeoutSeconds, DIRECTOR_STRATEGY_DEFAULTS.limits.claimTimeoutSeconds),
        director_phase_timeout_seconds: readPositiveInteger(strategy.limits.phaseTimeoutSeconds, DIRECTOR_STRATEGY_DEFAULTS.limits.phaseTimeoutSeconds),
        director_complete_timeout_seconds: readPositiveInteger(strategy.limits.completeTimeoutSeconds, DIRECTOR_STRATEGY_DEFAULTS.limits.completeTimeoutSeconds),
        director_task_timeout_seconds: readPositiveInteger(strategy.limits.taskTimeoutSeconds, DIRECTOR_STRATEGY_DEFAULTS.limits.taskTimeoutSeconds),
        director_forever: Boolean(strategy.observability.forever),
        director_show_output: Boolean(strategy.observability.showOutput),
    };
}
function normalizeMode(value) {
    return String(value || '').trim().toLowerCase() === 'serial' ? 'serial' : 'parallel';
}
function readPositiveInteger(value, fallback) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed))
        return fallback;
    return Math.max(1, Math.floor(parsed));
}
function formatStrategy(strategy) {
    return JSON.stringify(strategy, null, 2);
}
function createStrategyVersion(content, message, timestamp = new Date().toISOString()) {
    const parsed = parseStrategyContent(content);
    return {
        id: `${timestamp}-${message}`,
        version: parsed?.version || '1.0.0',
        content,
        timestamp,
        author: 'Polaris Desktop',
        message,
    };
}
function parseStrategyContent(content) {
    try {
        return JSON.parse(content);
    }
    catch {
        return null;
    }
}
export function DirectorStrategyPanel({ workspace, tasksCount = 0, runningTasks = 0, }) {
    const [activeView, setActiveView] = useState('editor');
    const [settingsSnapshot, setSettingsSnapshot] = useState(null);
    const [strategyJson, setStrategyJson] = useState(() => formatStrategy(buildDirectorStrategyFromSettings(null, workspace)));
    const [versions, setVersions] = useState([]);
    const [loading, setLoading] = useState(false);
    const [saveState, setSaveState] = useState('idle');
    const [message, setMessage] = useState(null);
    const [error, setError] = useState(null);
    const currentStrategy = useMemo(() => buildDirectorStrategyFromSettings(settingsSnapshot, workspace), [settingsSnapshot, workspace]);
    const displayWorkspace = workspaceLabel(workspace, '');
    const loadSettings = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const result = await settingsService.get();
            if (!result.ok || !result.data) {
                const detail = result.error || 'Director settings unavailable';
                setError(detail);
                setSaveState('error');
                setMessage(detail);
                return;
            }
            const strategy = buildDirectorStrategyFromSettings(result.data, workspace);
            const nextJson = formatStrategy(strategy);
            setSettingsSnapshot(result.data);
            setStrategyJson(nextJson);
            setVersions((prev) => {
                if (prev.some((item) => item.content === nextJson))
                    return prev;
                return [createStrategyVersion(nextJson, 'loaded'), ...prev].slice(0, 6);
            });
            setSaveState('idle');
            setMessage('已读取 /settings');
        }
        catch (err) {
            const detail = err instanceof Error ? err.message : 'Director settings unavailable';
            setError(detail);
            setSaveState('error');
            setMessage(detail);
        }
        finally {
            setLoading(false);
        }
    }, [workspace]);
    useEffect(() => {
        void loadSettings();
    }, [loadSettings]);
    const handleSaveStrategy = useCallback(async (strategy) => {
        const previousJson = strategyJson;
        const updatePayload = buildDirectorSettingsUpdateFromStrategy(strategy);
        setSaveState('saving');
        setMessage('正在同步到 /settings');
        setError(null);
        const result = await settingsService.update(updatePayload);
        if (!result.ok || !result.data) {
            const detail = result.error || 'Failed to update Director settings';
            setSaveState('error');
            setMessage(detail);
            setError(detail);
            throw new Error(detail);
        }
        const canonicalStrategy = {
            ...buildDirectorStrategyFromSettings(result.data, workspace),
            name: strategy.name,
            version: strategy.version,
        };
        const nextJson = formatStrategy(canonicalStrategy);
        setSettingsSnapshot(result.data);
        setStrategyJson(nextJson);
        setVersions((prev) => {
            const timeline = [
                createStrategyVersion(previousJson, 'before-save'),
                createStrategyVersion(nextJson, 'after-save'),
                ...prev,
            ];
            const deduped = timeline.filter((item, index, all) => (all.findIndex((candidate) => candidate.content === item.content) === index));
            return deduped.slice(0, 6);
        });
        setSaveState('saved');
        setMessage('已同步到 /settings');
    }, [strategyJson, workspace]);
    const statusLabel = loading
        ? 'loading'
        : error
            ? 'error'
            : saveState === 'saved'
                ? 'synced'
                : 'ready';
    return (_jsxs("section", { className: "flex h-full flex-col overflow-hidden bg-[linear-gradient(165deg,rgba(15,23,42,0.98),rgba(30,27,75,0.70),rgba(8,15,31,0.98))]", "data-testid": "director-strategy-panel", children: [_jsxs("header", { className: "flex min-h-16 items-center justify-between gap-4 border-b border-indigo-400/[0.15] px-4 py-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-3", children: [_jsx("div", { className: "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-cyan-400/25 bg-cyan-500/10", children: _jsx(SlidersHorizontal, { className: "h-4 w-4 text-cyan-200" }) }), _jsxs("div", { className: "min-w-0", children: [_jsx("h2", { className: "text-sm font-semibold text-slate-100", children: "Director \u7B56\u7565\u63A7\u5236" }), _jsxs("div", { className: "mt-1 flex min-w-0 flex-wrap items-center gap-2 text-[10px] text-slate-400", children: [_jsx("span", { className: "rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-cyan-200", children: "/settings" }), _jsxs("span", { "data-testid": "director-strategy-workspace-label", className: "max-w-[220px] truncate rounded border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono", title: workspace, "data-workspace-path": workspace, children: ["workspace=", displayWorkspace] })] })] })] }), _jsxs("div", { className: "flex shrink-0 items-center gap-2", children: [_jsx(MetricPill, { label: "mode", value: currentStrategy.mode, tone: currentStrategy.mode === 'parallel' ? 'cyan' : 'slate' }), _jsx(MetricPill, { label: "tasks", value: `${runningTasks}/${tasksCount}`, tone: runningTasks > 0 ? 'emerald' : 'slate' }), _jsxs("div", { className: cn('flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]', error
                                    ? 'border-red-500/25 bg-red-500/10 text-red-200'
                                    : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'), "data-testid": "director-strategy-status", children: [loading ? (_jsx(Loader2, { className: "h-3 w-3 animate-spin" })) : error ? (_jsx(AlertTriangle, { className: "h-3 w-3" })) : (_jsx(CheckCircle2, { className: "h-3 w-3" })), statusLabel] }), _jsx(Button, { type: "button", variant: "ghost", size: "icon", onClick: () => { void loadSettings(); }, disabled: loading || saveState === 'saving', title: "\u5237\u65B0 Director \u7B56\u7565\u8BBE\u7F6E", className: "h-8 w-8 text-slate-400 hover:bg-indigo-500/10 hover:text-indigo-200", children: _jsx(RefreshCw, { className: cn('h-4 w-4', loading && 'animate-spin') }) })] })] }), _jsxs("div", { className: "flex min-h-11 items-center justify-between gap-3 border-b border-white/10 bg-slate-950/40 px-4 py-2", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(ViewTab, { active: activeView === 'editor', icon: _jsx(Settings2, { className: "h-3.5 w-3.5" }), label: "\u7F16\u8F91", onClick: () => setActiveView('editor') }), _jsx(ViewTab, { active: activeView === 'diff', icon: _jsx(GitCompare, { className: "h-3.5 w-3.5" }), label: "\u5BF9\u6BD4", onClick: () => setActiveView('diff') })] }), _jsx("div", { className: "min-w-0 truncate text-[11px] text-slate-400", "data-testid": "director-strategy-message", children: message || '策略保存后会立即写入后端运行设置' })] }), _jsx("div", { className: "min-h-0 flex-1 overflow-hidden", children: activeView === 'editor' ? (_jsx(StrategyEditorPanel, { initialStrategy: strategyJson, onSave: handleSaveStrategy, saveState: saveState, saveMessage: message, saveButtonLabel: "\u5E94\u7528" })) : (_jsx(StrategyDiffViewer, { versions: versions, splitView: true })) })] }));
}
function MetricPill({ label, value, tone, }) {
    const toneClass = tone === 'cyan'
        ? 'border-cyan-400/25 bg-cyan-500/10 text-cyan-200'
        : tone === 'emerald'
            ? 'border-emerald-400/25 bg-emerald-500/10 text-emerald-200'
            : 'border-white/10 bg-white/5 text-slate-300';
    return (_jsxs("div", { className: cn('flex items-center gap-1.5 rounded border px-2 py-1 text-[10px]', toneClass), children: [_jsx("span", { className: "text-slate-500", children: label }), _jsx("span", { className: "font-mono", children: value })] }));
}
function ViewTab({ active, icon, label, onClick, }) {
    return (_jsxs("button", { type: "button", onClick: onClick, className: cn('flex h-7 items-center gap-1.5 rounded border px-2 text-xs transition-colors', active
            ? 'border-indigo-400/30 bg-indigo-500/[0.15] text-indigo-100'
            : 'border-white/10 bg-white/[0.03] text-slate-400 hover:bg-white/5 hover:text-slate-200'), children: [icon, label] }));
}
