import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from 'react';
import { Anchor, Play, Square, Settings, FolderOpen, RefreshCw, Zap, Loader2, FastForward, FileText, Brain, Activity, TerminalSquare, Crown, Hammer, MoreHorizontal, Bot, ClipboardList, Gauge, ShieldAlert, Network, Files } from 'lucide-react';
import { WindowControls } from './WindowControls';
import { UI_TERMS } from '@/app/constants/uiTerminology';
import { MiniStatusBadge } from '@/app/components/ai-dialogue/ManusStyleStatusIndicator';
import { cleanRuntimeDisplayText } from '@/app/utils/runtimeDisplay';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger, } from './ui/dropdown-menu';
const RUNTIME_PUSH_ENDPOINT = '/v2/ws/runtime';
const RUNTIME_PUSH_WAITING_MESSAGE = 'command submitted · waiting runtime.v2 push';
function processEvidenceText(evidence) {
    if (evidence.loading) {
        return `${RUNTIME_PUSH_ENDPOINT} · submitting command`;
    }
    if (evidence.error) {
        return `${RUNTIME_PUSH_ENDPOINT} · ${evidence.error}`;
    }
    return `${RUNTIME_PUSH_ENDPOINT} · ${evidence.message || RUNTIME_PUSH_WAITING_MESSAGE}`;
}
function processEvidenceSummary(evidence) {
    if (evidence.loading) {
        return 'submitting';
    }
    if (evidence.error) {
        return evidence.error;
    }
    return evidence.message || 'waiting runtime.v2';
}
export function ControlPanel({ workspace, pmRunning, directorRunning, pmToggleDisabled, pmBlockedReason, directorToggleDisabled, directorBlockedReason, runOnceDisabled, runOnceBlockedReason, agentsNeeded, agentsDraftReady, agentsDraftFailed, onOpenAgentsReview, onGenerateAgentsDraft, onOpenSettings, onPickWorkspace, onTogglePm, onRunPmOnce, onResumePm, onToggleDirector, onStopOllama, onRefresh, onOpenBrain, onEnterPMWorkspace, onEnterChiefEngineerWorkspace, onEnterDirectorWorkspace, onEnterFactoryMode, onEnterAGIWorkspace, onEnterRuntimeDiagnostics, onEnterContextOS, onEnterFiles, onOpenIntervention, workspaceError, isStartingPM, isStoppingPM, isStartingDirector, isStoppingDirector, isStoppingOllama, healthStatus, healthStatusDetail, onPingHealth, onOpenLogs, isArtifactsOpen, onToggleArtifacts, usageStats, ioFsyncMode, memoryRefsMode, onToggleTerminal, isTerminalOpen, currentPhase, currentTask, isExecutingTool, currentToolName, }) {
    const [moreMenuOpen, setMoreMenuOpen] = useState(false);
    const [pmToggleEvidence, setPmToggleEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const [directorToggleEvidence, setDirectorToggleEvidence] = useState({
        triggered: false,
        loading: false,
        message: null,
        error: null,
    });
    const pmDisabled = !!pmToggleDisabled;
    const directorDisabled = !!directorToggleDisabled;
    const runOnceBlocked = !!runOnceDisabled;
    const pmDisabledTitle = pmDisabled && pmBlockedReason
        ? pmBlockedReason
        : pmRunning
            ? UI_TERMS.actions.stopLoop
            : UI_TERMS.actions.startLoop;
    const runOnceTitle = runOnceBlocked && runOnceBlockedReason ? runOnceBlockedReason : UI_TERMS.actions.runOnce;
    const pmToggleBusy = Boolean(isStartingPM || isStoppingPM || pmToggleEvidence.loading);
    const directorToggleBusy = Boolean(isStartingDirector || isStoppingDirector || directorToggleEvidence.loading);
    const showAgents = !!agentsNeeded;
    const agentsReady = !!agentsDraftReady || !!agentsDraftFailed;
    const normalizedIoMode = ioFsyncMode === 'relaxed' ? 'RELAXED' : ioFsyncMode ? 'STRICT' : '';
    const normalizedMemMode = memoryRefsMode === 'off' ? 'OFF' : memoryRefsMode === 'soft' ? 'SOFT' : memoryRefsMode ? 'STRICT' : '';
    const ioTone = ioFsyncMode === 'relaxed'
        ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
        : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
    const memTone = memoryRefsMode === 'off'
        ? 'border-red-500/30 bg-red-500/10 text-red-200'
        : memoryRefsMode === 'soft'
            ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
            : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200';
    const displayCurrentTask = cleanRuntimeDisplayText(currentTask);
    const displayCurrentToolName = cleanRuntimeDisplayText(currentToolName);
    const displayWorkspace = workspaceLabel(workspace, '');
    const healthTone = healthStatus === 'unhealthy'
        ? 'bg-status-error text-status-error'
        : healthStatus === 'healthy' || healthStatus === 'ok'
            ? 'bg-status-success text-status-success'
            : 'bg-status-warning text-status-warning';
    const healthLabel = healthStatus === 'unhealthy'
        ? UI_TERMS.states.offline
        : healthStatus === 'checking'
            ? UI_TERMS.states.pinging
            : healthStatus
                ? UI_TERMS.states.ready
                : UI_TERMS.states.pinging;
    // 计算当前状态指示
    const getStatusIndicator = () => {
        if (pmRunning || directorRunning) {
            if (currentPhase) {
                return { phase: currentPhase, task: currentTask, tool: currentToolName };
            }
            return { phase: pmRunning ? 'pm_running' : 'director_running', task: undefined, tool: undefined };
        }
        return { phase: 'idle', task: undefined, tool: undefined };
    };
    const statusInfo = getStatusIndicator();
    const runMoreMenuAction = (action) => {
        setMoreMenuOpen(false);
        void action?.();
    };
    const openInterventionCenter = () => {
        if (onOpenIntervention) {
            onOpenIntervention();
            return;
        }
        window.dispatchEvent(new CustomEvent('open-intervention-center'));
    };
    const handleTogglePm = async () => {
        setPmToggleEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onTogglePm());
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
                error: accepted === false ? 'PM command was not accepted' : null,
            });
        }
        catch (error) {
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'PM status unavailable',
            });
        }
    };
    const handleRunPmOnce = async () => {
        if (!onRunPmOnce) {
            return;
        }
        setPmToggleEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onRunPmOnce());
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
                error: accepted === false ? 'PM command was not accepted' : null,
            });
        }
        catch (error) {
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'PM status unavailable',
            });
        }
    };
    const handleResumePm = async () => {
        if (!onResumePm) {
            return;
        }
        setPmToggleEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onResumePm());
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
                error: accepted === false ? 'PM command was not accepted' : null,
            });
        }
        catch (error) {
            setPmToggleEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'PM status unavailable',
            });
        }
    };
    const handleToggleDirector = async () => {
        setDirectorToggleEvidence({
            triggered: true,
            loading: true,
            message: null,
            error: null,
        });
        try {
            const accepted = await Promise.resolve(onToggleDirector());
            setDirectorToggleEvidence({
                triggered: true,
                loading: false,
                message: accepted === false ? 'command not accepted' : RUNTIME_PUSH_WAITING_MESSAGE,
                error: accepted === false ? 'Director command was not accepted' : null,
            });
        }
        catch (error) {
            setDirectorToggleEvidence({
                triggered: true,
                loading: false,
                message: null,
                error: error instanceof Error ? error.message : 'Director status unavailable',
            });
        }
    };
    return (_jsxs("header", { className: "panel-header z-50 relative", children: [_jsxs("div", { className: "flex items-center gap-4", children: [_jsx(WindowControls, {}), _jsx("div", { className: "w-px h-4 bg-white/10" }), _jsxs("div", { className: "flex items-center gap-2 group", children: [_jsx("div", { className: "relative", children: _jsx("button", { onClick: onToggleArtifacts, "data-testid": "control-panel-toggle-monitor", className: `p-2 rounded-lg transition-colors ${isArtifactsOpen
                                        ? 'text-accent bg-accent/10 hover:bg-accent/20'
                                        : 'text-text-muted hover:text-text-main hover:bg-white/5'}`, title: isArtifactsOpen ? '收起监控面板' : '展开监控面板', children: _jsx(Activity, { className: "size-5" }) }) }), _jsx("div", { className: "relative group/ws", children: _jsx(Anchor, { className: "size-6 text-accent relative z-10" }) }), _jsxs("div", { children: [_jsx("h1", { className: "font-heading font-bold text-xl text-text-main tracking-tight", children: "Polaris" }), _jsx("p", { className: "text-[10px] text-text-dim font-mono tracking-wider uppercase", children: "\u8D1E\u89C2\u6CD5\u5EA6 \u00B7 \u67A2\u5BC6\u4E2D\u53F0" })] })] })] }), _jsxs("div", { className: "flex-1 max-w-[26rem] mx-6 relative group", children: [_jsxs("div", { className: `no-drag flex items-center gap-2 soft-inset rounded-lg px-3 py-1.5 transition-all duration-300 ${workspaceError ? 'border-status-error/60' : 'group-hover:border-accent/30'}`, title: workspaceError || workspace || undefined, children: [onPickWorkspace ? (_jsx("button", { type: "button", onClick: onPickWorkspace, className: "text-text-muted hover:text-accent transition-colors", "aria-label": `浏览并选定${UI_TERMS.nouns.workspace}`, title: `浏览并选定${UI_TERMS.nouns.workspace}`, children: _jsx(FolderOpen, { className: "size-4" }) })) : (_jsx(FolderOpen, { className: "size-4 text-text-dim" })), _jsx("input", { type: "text", "data-testid": "control-panel-workspace-label", value: displayWorkspace, readOnly: true, title: workspace || undefined, "data-workspace-path": workspace || undefined, className: "min-w-0 flex-1 bg-transparent text-sm text-text-main outline-none font-sans placeholder:text-text-dim/50 cursor-default", placeholder: `请点击左侧按钮选定${UI_TERMS.nouns.workspace}（Workspace）...`, "aria-invalid": workspaceError ? true : undefined, "aria-describedby": workspaceError ? 'workspace-error' : undefined })] }), workspaceError ? (_jsx("div", { id: "workspace-error", className: "absolute left-0 right-0 top-full mt-1 text-xs text-status-error bg-bg-panel border border-status-error/30 rounded px-2 py-1 shadow-md z-50", children: workspaceError })) : null] }), _jsxs("div", { className: "flex items-center gap-3", children: [_jsxs("div", { className: "no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg", children: [_jsx("span", { className: "text-[10px] uppercase font-bold text-text-dim tracking-wider px-1", children: UI_TERMS.roles.pm }), pmBlockedReason ? (_jsx("span", { className: "max-w-[170px] truncate rounded border border-status-error/20 bg-status-error/20 px-1.5 py-0.5 text-[10px] text-status-error", title: pmBlockedReason, children: pmBlockedReason })) : null, _jsx("button", { onClick: () => { void handleTogglePm(); }, "data-testid": "control-panel-pm-toggle", disabled: pmDisabled || pmToggleBusy, className: `p-1.5 rounded-md transition-all duration-300 relative ${pmRunning
                                    ? 'soft-raised text-accent'
                                    : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'} ${pmDisabled || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: pmDisabledTitle, children: pmToggleBusy ? (_jsx(Loader2, { className: "size-3.5 animate-spin" })) : pmRunning ? (_jsx(Square, { className: "size-3.5 fill-current" })) : (_jsx(Play, { className: "size-3.5 fill-current" })) }), pmToggleEvidence.triggered ? (_jsx("span", { "data-testid": "control-panel-pm-toggle-evidence", title: processEvidenceText(pmToggleEvidence), "data-endpoint": RUNTIME_PUSH_ENDPOINT, "data-evidence": processEvidenceText(pmToggleEvidence), className: `max-w-[170px] truncate rounded border px-1.5 py-0.5 font-mono text-[10px] ${pmToggleEvidence.error
                                    ? 'border-status-error/30 bg-status-error/10 text-status-error'
                                    : pmToggleEvidence.loading
                                        ? 'border-white/10 bg-white/5 text-text-muted'
                                        : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'}`, children: processEvidenceSummary(pmToggleEvidence) })) : null, onRunPmOnce ? (_jsx("button", { onClick: () => { void handleRunPmOnce(); }, "data-testid": "control-panel-pm-run-once", disabled: runOnceBlocked || pmToggleBusy, className: `p-1.5 rounded-md transition-colors text-text-muted hover:text-accent hover:bg-accent-dim relative ${runOnceBlocked || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: runOnceTitle, children: pmToggleBusy ? _jsx(Loader2, { className: "size-3.5 animate-spin" }) : _jsx(Zap, { className: "size-3.5" }) })) : null, onResumePm && !pmRunning ? (_jsx("button", { onClick: () => { void handleResumePm(); }, disabled: pmDisabled || pmToggleBusy, className: `p-1.5 rounded-md transition-colors text-text-muted hover:text-status-warning hover:bg-status-warning/10 relative ${pmDisabled || pmToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: UI_TERMS.actions.resumeLast, children: _jsx(FastForward, { className: "size-3.5" }) })) : null] }), _jsxs("div", { className: "no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg", children: [_jsx("span", { className: "text-[10px] uppercase font-bold text-text-dim tracking-wider px-1", children: UI_TERMS.roles.director }), directorBlockedReason ? (_jsx("span", { className: "text-[10px] px-1.5 py-0.5 rounded bg-status-error/20 text-status-error border border-status-error/20", children: directorBlockedReason })) : null, _jsx("button", { onClick: () => { void handleToggleDirector(); }, "data-testid": "control-panel-director-toggle", disabled: directorDisabled || directorToggleBusy, className: `p-1.5 rounded-md transition-all duration-300 relative ${directorRunning
                                    ? 'soft-raised text-accent'
                                    : 'bg-white/5 text-text-muted hover:bg-white/10 hover:text-text-main'} ${directorDisabled || directorToggleBusy ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: directorBlockedReason || undefined, children: directorToggleBusy ? (_jsx(Loader2, { className: "size-3.5 animate-spin" })) : directorRunning ? (_jsx(Square, { className: "size-3.5 fill-current" })) : (_jsx(Play, { className: "size-3.5 fill-current" })) }), directorToggleEvidence.triggered ? (_jsx("span", { "data-testid": "control-panel-director-toggle-evidence", title: processEvidenceText(directorToggleEvidence), "data-endpoint": RUNTIME_PUSH_ENDPOINT, "data-evidence": processEvidenceText(directorToggleEvidence), className: `max-w-[190px] truncate rounded border px-1.5 py-0.5 font-mono text-[10px] ${directorToggleEvidence.error
                                    ? 'border-status-error/30 bg-status-error/10 text-status-error'
                                    : directorToggleEvidence.loading
                                        ? 'border-white/10 bg-white/5 text-text-muted'
                                        : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'}`, children: processEvidenceSummary(directorToggleEvidence) })) : null] }), onEnterFactoryMode && (_jsx("button", { onClick: onEnterFactoryMode, "data-testid": "control-panel-enter-factory", className: "no-drag p-1.5 rounded-md transition-all soft-raised text-emerald-400", title: "Factory \u6A21\u5F0F - \u65E0\u4EBA\u503C\u5B88\u5F00\u53D1\u5DE5\u5382", children: _jsx(Hammer, { className: "size-3.5" }) })), onEnterContextOS && (_jsxs("button", { onClick: onEnterContextOS, "data-testid": "control-panel-enter-contextos", className: "no-drag flex items-center gap-1.5 px-2.5 py-1.5 rounded-md transition-all soft-chip text-text-muted hover:text-text-main", title: "ContextOS \u5B9E\u65F6\u89C6\u56FE - \u4E0A\u4E0B\u6587\u64CD\u4F5C\u7CFB\u7EDF\u6570\u636E\u6D41\u76D1\u63A7", children: [_jsx(Network, { className: "size-3.5" }), _jsx("span", { className: "text-[10px] font-bold uppercase tracking-wider", children: "ContextOS" })] })), onEnterFiles && (_jsxs("button", { onClick: onEnterFiles, "data-testid": "control-panel-enter-files", className: "no-drag flex items-center gap-1.5 px-2.5 py-1.5 rounded-md transition-all soft-chip text-text-muted hover:text-text-main", title: "Workspace \u6587\u4EF6\u6D4F\u89C8\u5668 - \u6D4F\u89C8\u9879\u76EE\u6587\u4EF6\u5E76\u9884\u89C8\u4EE3\u7801", children: [_jsx(Files, { className: "size-3.5" }), _jsx("span", { className: "text-[10px] font-bold uppercase tracking-wider", children: "Files" })] })), _jsxs("div", { className: "no-drag flex items-center gap-2 px-2.5 py-1 soft-panel-subtle rounded-lg", children: [(pmRunning || directorRunning) && displayCurrentTask && (_jsx("div", { className: "flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 max-w-[200px]", children: _jsx("span", { className: "text-[10px] text-amber-300 truncate", title: displayCurrentTask, children: isExecutingTool && displayCurrentToolName ? `工具: ${displayCurrentToolName}` : displayCurrentTask }) })), (pmRunning || directorRunning) && !displayCurrentTask && isExecutingTool && displayCurrentToolName && (_jsx("div", { className: "flex items-center gap-1.5 px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/30 max-w-[200px]", children: _jsxs("span", { className: "text-[10px] text-amber-300 truncate", title: displayCurrentToolName, children: ["\u6B63\u5728\u6267\u884C: ", displayCurrentToolName] }) })), (pmRunning || directorRunning || currentPhase) && (_jsx(MiniStatusBadge, { phase: isExecutingTool ? 'tool_running' :
                                    currentPhase === 'planning' ? 'thinking' :
                                        currentPhase === 'implementation' ? 'executing' :
                                            pmRunning || directorRunning ? 'executing' : 'idle', theme: pmRunning ? 'amber' : 'indigo' })), _jsxs("button", { onClick: onPingHealth, className: "group flex shrink-0 items-center gap-2 whitespace-nowrap transition-all", title: healthStatusDetail || '校验连通性', "data-testid": "control-panel-health-ping", children: [_jsx("div", { className: `size-1.5 rounded-full transition-colors duration-500 ${healthTone}` }), _jsx("span", { className: "whitespace-nowrap text-[10px] font-mono uppercase text-text-dim transition-colors group-hover:text-text-muted", children: healthLabel })] }), _jsx("div", { className: "w-px h-3 bg-white/10 mx-1" }), _jsxs("button", { onClick: onOpenLogs, "data-testid": "control-panel-open-logs", className: "flex items-center gap-1.5 text-text-dim hover:text-accent transition-colors", title: "\u67E5\u770B\u5B50\u8FDB\u7A0B\u4E0E\u56DE\u6267\u65E5\u5FD7", children: [_jsx(Activity, { className: "size-3.5" }), _jsx("span", { className: "text-[10px] font-bold uppercase tracking-widest", children: UI_TERMS.nouns.logs })] }), normalizedIoMode || normalizedMemMode ? (_jsxs(_Fragment, { children: [_jsx("div", { className: "w-px h-3 bg-white/10 mx-1" }), _jsxs("div", { className: "flex items-center gap-1", children: [normalizedIoMode ? (_jsxs("span", { className: `px-2 py-0.5 rounded border text-[9px] font-mono uppercase tracking-wider ${ioTone}`, children: ["IO:", normalizedIoMode] })) : null, normalizedMemMode ? (_jsxs("span", { className: `px-2 py-0.5 rounded border text-[9px] font-mono uppercase tracking-wider ${memTone}`, children: ["MEM:", normalizedMemMode] })) : null] })] })) : null] }), showAgents ? (_jsxs("div", { className: "no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg border border-status-warning/30", children: [_jsx("span", { className: "text-[10px] uppercase font-bold text-status-warning tracking-wider px-1", children: "AGENTS" }), _jsx("button", { onClick: agentsReady ? onOpenAgentsReview : onGenerateAgentsDraft, "data-testid": agentsReady ? 'control-panel-open-agents-review' : 'control-panel-generate-agents-draft', disabled: agentsReady ? !onOpenAgentsReview : !onGenerateAgentsDraft, className: `p-1.5 rounded-md transition-colors relative ${agentsReady
                                    ? 'bg-status-warning/20 text-status-warning hover:bg-status-warning/30'
                                    : 'bg-accent/20 text-accent hover:bg-accent/30'} ${(!onOpenAgentsReview && agentsReady) || (!onGenerateAgentsDraft && !agentsReady) ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: agentsReady ? '打开 AGENTS 审阅' : '生成 AGENTS 草拟稿', children: _jsx(FileText, { className: "size-3.5" }) })] })) : null, onStopOllama ? (_jsxs("div", { className: "no-drag flex items-center gap-1.5 px-2 py-1 soft-panel-subtle rounded-lg", children: [_jsx("span", { className: "text-[10px] uppercase font-bold text-text-dim tracking-wider px-1", children: "Ollama" }), _jsx("button", { onClick: onStopOllama, disabled: isStoppingOllama, className: `p-1.5 rounded-md transition-colors bg-status-error/10 text-status-error hover:bg-status-error/20 relative ${isStoppingOllama ? 'opacity-50 cursor-not-allowed hover:bg-transparent' : ''}`, title: "\u505C\u7528 Ollama \u6A21\u578B", children: isStoppingOllama ? _jsx(Loader2, { className: "size-3.5 animate-spin" }) : _jsx(Square, { className: "size-3.5" }) })] })) : null, _jsx("div", { className: "w-px h-6 bg-white/10 mx-1" }), _jsxs(DropdownMenu, { open: moreMenuOpen, onOpenChange: setMoreMenuOpen, children: [_jsx(DropdownMenuTrigger, { asChild: true, children: _jsx("button", { className: "btn-icon", "data-testid": "control-panel-more-menu", title: "\u66F4\u591A\u529F\u80FD", children: _jsx(MoreHorizontal, { className: "size-4" }) }) }), _jsxs(DropdownMenuContent, { align: "end", className: "w-48", children: [onEnterPMWorkspace && (_jsxs(DropdownMenuItem, { "data-testid": "enter-pm-workspace", onClick: () => runMoreMenuAction(onEnterPMWorkspace), children: [_jsx(Crown, { className: "size-4 mr-2" }), "PM \u5DE5\u4F5C\u533A"] })), onEnterChiefEngineerWorkspace && (_jsxs(DropdownMenuItem, { "data-testid": "enter-chief-engineer-workspace", onClick: () => runMoreMenuAction(onEnterChiefEngineerWorkspace), children: [_jsx(ClipboardList, { className: "size-4 mr-2" }), "Chief Engineer \u5DE5\u4F5C\u533A"] })), onEnterDirectorWorkspace && (_jsxs(DropdownMenuItem, { "data-testid": "enter-director-workspace", onClick: () => runMoreMenuAction(onEnterDirectorWorkspace), children: [_jsx(Hammer, { className: "size-4 mr-2" }), "Director \u5DE5\u4F5C\u533A"] })), onEnterAGIWorkspace && (_jsxs(DropdownMenuItem, { "data-testid": "enter-agi-workspace", onClick: () => runMoreMenuAction(onEnterAGIWorkspace), children: [_jsx(Bot, { className: "size-4 mr-2" }), "AGI \u5DE5\u4F5C\u533A"] })), onEnterRuntimeDiagnostics && (_jsxs(DropdownMenuItem, { "data-testid": "enter-runtime-diagnostics", onClick: () => runMoreMenuAction(onEnterRuntimeDiagnostics), children: [_jsx(Gauge, { className: "size-4 mr-2" }), "\u8FD0\u884C\u8BCA\u65AD"] })), onEnterContextOS && (_jsxs(DropdownMenuItem, { "data-testid": "enter-contextos-menu-item", onClick: () => runMoreMenuAction(onEnterContextOS), children: [_jsx(Network, { className: "size-4 mr-2" }), "ContextOS \u5B9E\u65F6\u89C6\u56FE"] })), onEnterFiles && (_jsxs(DropdownMenuItem, { "data-testid": "enter-files-menu-item", onClick: () => runMoreMenuAction(onEnterFiles), children: [_jsx(Files, { className: "size-4 mr-2" }), "Workspace \u6587\u4EF6\u6D4F\u89C8\u5668"] })), showAgents && (_jsxs(DropdownMenuItem, { "data-testid": agentsReady ? 'open-agents-review-menu-item' : 'generate-agents-menu-item', onClick: () => runMoreMenuAction(agentsReady ? onOpenAgentsReview : onGenerateAgentsDraft), children: [_jsx(FileText, { className: "size-4 mr-2" }), agentsReady ? 'AGENTS 审阅' : '生成 AGENTS'] })), onOpenBrain && (_jsxs(DropdownMenuItem, { "data-testid": "open-brain-menu-item", onClick: () => runMoreMenuAction(onOpenBrain), children: [_jsx(Brain, { className: "size-4 mr-2" }), "\u660E\u955C\u53F0 (Brain)"] })), _jsx(DropdownMenuSeparator, {}), _jsxs(DropdownMenuItem, { "data-testid": "open-intervention-center-menu-item", onClick: () => runMoreMenuAction(openInterventionCenter), children: [_jsx(ShieldAlert, { className: "size-4 mr-2" }), "\u5E72\u9884\u4E2D\u5FC3"] }), onStopOllama && (_jsxs(DropdownMenuItem, { onClick: () => runMoreMenuAction(onStopOllama), disabled: isStoppingOllama, children: [_jsx(Square, { className: "size-4 mr-2" }), isStoppingOllama ? '停用中...' : '停用 Ollama'] }))] })] }), _jsx("button", { className: "btn-icon", onClick: onRefresh, "data-testid": "control-panel-refresh", title: "\u5237\u65B0\u8FD0\u884C\u72B6\u6001", children: _jsx(RefreshCw, { className: "size-4" }) }), _jsx("button", { onClick: onOpenSettings, "data-testid": "control-panel-open-settings", className: "btn-icon", title: UI_TERMS.actions.openSettings, children: _jsx(Settings, { className: "size-4" }) }), _jsx("div", { className: "w-px h-6 bg-white/10 mx-1" }), _jsx("div", { className: "w-px h-6 bg-white/10 mx-1" }), _jsx("button", { onClick: onToggleTerminal, "data-testid": "control-panel-toggle-terminal", className: `btn-icon group relative ${isTerminalOpen ? 'text-emerald-400 bg-emerald-400/10' : ''}`, title: "Terminal (Ctrl + `)", children: _jsx(TerminalSquare, { className: "size-4" }) }), _jsx("div", { className: "w-px h-6 bg-white/10 mx-1" })] })] }));
}
