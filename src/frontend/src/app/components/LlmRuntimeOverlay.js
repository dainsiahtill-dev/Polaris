import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useMemo, useState } from 'react';
import { Activity, Bot, ChevronDown, ChevronUp, Cpu, FileCode, GitBranch, Loader2, Radar, Sparkles, Terminal, Wifi, WifiOff, Zap, } from 'lucide-react';
import { StatusBadge } from '@/app/components/ui/badge';
import { cn } from '@/app/components/ui/utils';
import { filterExecutionActivityLogs } from '@/app/utils/appRuntime';
function viewLabel(activeView) {
    if (activeView === 'agi') {
        return 'AGI';
    }
    if (activeView === 'chief_engineer') {
        return 'CE';
    }
    if (activeView === 'diagnostics') {
        return 'DIAG';
    }
    return activeView.toUpperCase();
}
const PHASE_LABELS = {
    idle: '空闲',
    agents: 'AGENTS 审核',
    planning: 'Planning',
    analyzing: '任务分析',
    executing: 'Executing',
    llm_calling: 'LLM 推理',
    tool_running: '工具执行',
    verification: '验证中',
    chief_engineer: 'Chief Engineer Design',
    director: 'Director 执行',
    qa: 'QA 验收',
    completed: '已完成',
    complete: '已完成',
    failed: '执行失败',
    error: '执行失败',
};
function overlayQualityGateDisplay(projection, qualityGate) {
    if (projection) {
        if (!projection.available) {
            return { label: 'Run Ledger HOLD', tone: 'fail' };
        }
        if (projection.total <= 0 && projection.projects.length === 0) {
            return { label: 'Run Ledger PENDING', tone: 'hold' };
        }
        const failedProject = projection.projects.find((project) => !project.ok || project.failed_gate_count > 0);
        if (!projection.ok || projection.failed > 0 || failedProject) {
            return { label: 'Run Ledger FAIL', tone: 'fail' };
        }
        return { label: `Run Ledger PASS ${projection.projected}/${projection.total}`, tone: 'pass' };
    }
    if (!qualityGate)
        return null;
    if (qualityGate.passed) {
        return { label: 'Run Ledger PENDING', tone: 'hold' };
    }
    return {
        label: `${qualityGate.score}/100`,
        tone: qualityGate.passed ? 'pass' : 'hold',
    };
}
function overlayQualityGateToneClass(tone) {
    if (tone === 'pass')
        return 'text-emerald-300';
    if (tone === 'fail')
        return 'text-red-300';
    return 'text-amber-300';
}
function normalizeStateToken(value) {
    const token = String(value || '').trim().toLowerCase();
    if (token === 'ready')
        return 'ready';
    if (token === 'blocked')
        return 'blocked';
    return 'unknown';
}
function isActiveRuntimePhase(value) {
    const token = String(value || '').trim().toLowerCase();
    return [
        'agents',
        'planning',
        'analyzing',
        'executing',
        'llm_calling',
        'tool_running',
        'verification',
        'chief_engineer',
        'director',
        'qa',
    ].includes(token);
}
function normalizeDisplayPhase(value, running, factoryRuntimeActive) {
    const token = String(value || '').trim().toLowerCase();
    if (!running && !factoryRuntimeActive && ['error', 'failed', 'blocked', 'cancelled', 'canceled'].includes(token)) {
        return 'idle';
    }
    return value;
}
function toRelativeTime(value) {
    if (!value)
        return '未更新';
    const epoch = Date.parse(value);
    if (!Number.isFinite(epoch))
        return '未更新';
    const seconds = Math.max(0, Math.floor((Date.now() - epoch) / 1000));
    if (seconds < 60)
        return `${seconds}s 前`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60)
        return `${minutes}m 前`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h 前`;
}
function toEpoch(value) {
    const parsed = Date.parse(String(value || '').trim());
    return Number.isFinite(parsed) ? parsed : 0;
}
function isLowSignalLog(log) {
    const text = `${log.source} ${log.message} ${log.details || ''}`.toLowerCase();
    if (isStructuredRuntimeFragment(log))
        return true;
    if (text.includes('initialized docs via onboarding wizard'))
        return true;
    if (/\[history\]\s*archived round/.test(text))
        return true;
    if (/\[runtime\]\s*workspace=/.test(text))
        return true;
    return false;
}
function isStructuredRuntimeFragment(log) {
    const source = String(log.source || '').trim().toLowerCase();
    if (!/(engine|runtime|system)/.test(source))
        return false;
    const message = String(log.message || '').trim();
    if (!message)
        return true;
    if (/^\[object(?:\s+object)?\]$/i.test(message))
        return true;
    if (/^[{}\[\],]+$/.test(message))
        return true;
    if (/^["']?[}\]],?$/.test(message))
        return true;
    if (/^:\d{2}(?:\.\d+)?z["']?,?$/i.test(message))
        return true;
    return /^["']?[a-z0-9_.-]+["']?\s*:\s*(?:$|["'{\[\]\d]|true\b|false\b|null\b)/i.test(message);
}
function isStructuredRuntimeText(value) {
    return isStructuredRuntimeFragment({
        id: 'inline',
        timestamp: new Date(0).toISOString(),
        source: 'Engine',
        level: 'info',
        message: value || '',
    });
}
function logPriority(log) {
    const streamEvent = getStreamEvent(log);
    const text = `${log.source} ${log.message} ${log.details || ''}`.toLowerCase();
    let score = 0;
    if (/llm|invoke|tool|质量|qa|director|pm/.test(text))
        score += 3;
    if (streamEvent === 'tool_call' || streamEvent === 'tool_result')
        score += 4;
    if (streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk')
        score += 2;
    if (log.level === 'thinking')
        score += 3;
    if (log.level === 'warning')
        score += 2;
    if (log.level === 'error')
        score += 4;
    if (isLowSignalLog(log))
        score -= 5;
    return score;
}
function getStreamEvent(log) {
    return String(log.meta?.streamEvent || '').toLowerCase();
}
function streamEventLabel(log) {
    const streamEvent = getStreamEvent(log);
    if (streamEvent === 'thinking_chunk')
        return '思';
    if (streamEvent === 'content_chunk')
        return '输出';
    if (streamEvent === 'tool_call')
        return '工具';
    if (streamEvent === 'tool_result')
        return '结果';
    return '';
}
function displayLogMessage(log) {
    const message = String(log.message || '').trim();
    if (!/^\[object(?:\s+object)?\]$/i.test(message)) {
        return message;
    }
    return String(log.title || streamEventLabel(log) || getStreamEvent(log) || '结构化事件').trim();
}
function streamEventIcon(log) {
    const streamEvent = getStreamEvent(log);
    if (streamEvent === 'thinking_chunk')
        return _jsx(Zap, { className: "size-3 text-amber-400" });
    if (streamEvent === 'content_chunk')
        return _jsx(Bot, { className: "size-3 text-cyan-400" });
    if (streamEvent === 'tool_call')
        return _jsx(Terminal, { className: "size-3 text-green-400" });
    if (streamEvent === 'tool_result')
        return _jsx(GitBranch, { className: "size-3 text-emerald-400" });
    return _jsx(Cpu, { className: "size-3 text-text-dim" });
}
function streamEventStyle(log) {
    const streamEvent = getStreamEvent(log);
    if (streamEvent === 'thinking_chunk')
        return 'border-amber-400/30 bg-amber-500/10';
    if (streamEvent === 'content_chunk')
        return 'border-cyan-400/30 bg-cyan-500/10';
    if (streamEvent === 'tool_call')
        return 'border-green-400/30 bg-green-500/10';
    if (streamEvent === 'tool_result')
        return 'border-emerald-400/30 bg-emerald-500/10';
    return 'border-border bg-white/45';
}
function isTypingStreamEvent(log) {
    const streamEvent = getStreamEvent(log);
    return streamEvent === 'thinking_chunk' || streamEvent === 'content_chunk';
}
function TypingMessage({ text, animate, }) {
    const [visibleChars, setVisibleChars] = useState(() => text.length);
    useEffect(() => {
        if (!animate) {
            setVisibleChars(text.length);
            return;
        }
        setVisibleChars((current) => {
            // Reset animation when message shrinks (e.g. truncation/rotation), otherwise continue.
            if (text.length < current)
                return 0;
            return Math.min(current, text.length);
        });
    }, [animate, text]);
    useEffect(() => {
        if (!animate)
            return;
        if (visibleChars >= text.length)
            return;
        // UI-only typewriter animation; runtime data still arrives via WebSocket.
        const timer = window.setInterval(() => {
            setVisibleChars((current) => {
                if (current >= text.length)
                    return current;
                // Reveal in small batches for smoother but still "token-like" animation.
                return Math.min(text.length, current + 2);
            });
        }, 16);
        return () => {
            window.clearInterval(timer);
        };
    }, [animate, text, visibleChars]);
    const rendered = animate ? text.slice(0, visibleChars) : text;
    const showCursor = animate && visibleChars < text.length;
    return (_jsxs("div", { className: "text-[10px] text-text-muted", children: [_jsx("span", { children: rendered }), showCursor && _jsx("span", { className: "ml-[1px] inline-block animate-pulse text-accent", children: "\u258B" })] }));
}
function pickHeadline(active, latestLog, currentPhase) {
    if (latestLog)
        return displayLogMessage(latestLog);
    if (active)
        return `正在执行 ${PHASE_LABELS[currentPhase] || currentPhase || '流程'}...`;
    return '系统待命';
}
export function LlmRuntimeOverlay({ activeView, websocketLive, websocketReconnecting, websocketAttemptCount, pmRunning, directorRunning, llmState, llmBlockedRoles, llmRequiredRoles, llmLastUpdated, factoryRuntimeActive = false, currentPhase, qualityGate, executionLogs, llmStreamEvents, processStreamEvents, fileEditEvents = [], controlPlaneProjection, }) {
    const [expanded, setExpanded] = useState(false);
    const compactFactoryMode = activeView === 'factory';
    const roleWorkspaceMode = activeView === 'pm' || activeView === 'chief_engineer' || activeView === 'director';
    const running = pmRunning || directorRunning;
    const llmStateToken = normalizeStateToken(llmState);
    const runtimeActive = running || factoryRuntimeActive || isActiveRuntimePhase(currentPhase);
    const factoryBlockedRoleVisible = factoryRuntimeActive && llmBlockedRoles.some((role) => (['pm', 'chief_engineer', 'director', 'qa'].includes(role)));
    const blockedRoleForView = (activeView === 'pm' && llmBlockedRoles.includes('pm')) ||
        (activeView === 'director' && llmBlockedRoles.includes('director')) ||
        (activeView === 'chief_engineer' && llmBlockedRoles.includes('chief_engineer')) ||
        (activeView === 'factory' && factoryBlockedRoleVisible);
    const isLlmBlocked = llmStateToken === 'blocked' && (runtimeActive || blockedRoleForView);
    const connectionOnly = !runtimeActive && !isLlmBlocked && (websocketReconnecting || !websocketLive);
    const shouldRenderOverlay = runtimeActive ||
        websocketReconnecting ||
        !websocketLive ||
        isLlmBlocked;
    const displayPhase = normalizeDisplayPhase(currentPhase, running, factoryRuntimeActive);
    const phaseLabel = (PHASE_LABELS[displayPhase] ||
        (pmRunning && !directorRunning ? 'PM Running' : '') ||
        (directorRunning ? 'Director 执行中' : '') ||
        displayPhase ||
        '等待中');
    useEffect(() => {
        if (compactFactoryMode || roleWorkspaceMode) {
            setExpanded(false);
        }
    }, [compactFactoryMode, roleWorkspaceMode]);
    useEffect(() => {
        if (compactFactoryMode || roleWorkspaceMode) {
            return;
        }
        if (running || isLlmBlocked) {
            setExpanded(true);
        }
        else if (connectionOnly) {
            setExpanded(false);
        }
    }, [compactFactoryMode, roleWorkspaceMode, running, isLlmBlocked, connectionOnly]);
    const recentSteps = useMemo(() => {
        const now = Date.now();
        const freshnessWindowMs = running ? 20 * 60 * 1000 : 24 * 60 * 60 * 1000;
        const processExecutionLogs = filterExecutionActivityLogs(processStreamEvents);
        const ordered = [...llmStreamEvents, ...processExecutionLogs, ...executionLogs]
            .filter((entry) => Boolean(String(entry.message || '').trim()))
            .sort((a, b) => toEpoch(a.timestamp) - toEpoch(b.timestamp));
        const fresh = ordered.filter((entry) => {
            const ts = toEpoch(entry.timestamp);
            return ts > 0 && now - ts <= freshnessWindowMs;
        });
        const candidates = fresh.length > 0 ? fresh : running ? [] : ordered.slice(-32);
        const filtered = candidates.filter((entry) => !isLowSignalLog(entry));
        const hasLowSignalLogs = candidates.some(isLowSignalLog);
        const hasStructuredFragments = candidates.some(isStructuredRuntimeFragment);
        const pool = filtered.length > 0 || hasStructuredFragments || hasLowSignalLogs ? filtered : candidates;
        const ranked = [...pool].sort((a, b) => {
            const tsDiff = toEpoch(b.timestamp) - toEpoch(a.timestamp);
            if (tsDiff !== 0)
                return tsDiff;
            return logPriority(b) - logPriority(a);
        });
        const deduped = [];
        const seen = new Set();
        for (const entry of ranked) {
            const key = `${entry.source}|${displayLogMessage(entry)}`;
            if (seen.has(key))
                continue;
            seen.add(key);
            deduped.push(entry);
            if (deduped.length >= 6)
                break;
        }
        return deduped;
    }, [executionLogs, llmStreamEvents, processStreamEvents, running]);
    const latestFileEdit = useMemo(() => {
        return [...fileEditEvents]
            .filter((event) => Boolean(event.filePath))
            .sort((a, b) => toEpoch(b.timestamp) - toEpoch(a.timestamp))[0] || null;
    }, [fileEditEvents]);
    const qualityGateDisplay = overlayQualityGateDisplay(controlPlaneProjection, qualityGate);
    const latestStep = recentSteps[0] ?? null;
    const headline = pickHeadline(running, latestStep, displayPhase);
    const effectiveUpdateTime = latestStep?.timestamp || llmLastUpdated || null;
    const visibleRequiredRoles = running || isLlmBlocked ? llmRequiredRoles : [];
    const visibleBlockedRoles = isLlmBlocked ? llmBlockedRoles : [];
    const llmBadgeColor = llmStateToken === 'ready' ? 'success' : isLlmBlocked ? 'error' : running ? 'warning' : 'default';
    const llmBadgeLabel = llmStateToken === 'ready' ? 'LLM READY' : isLlmBlocked ? 'LLM BLOCKED' : running ? 'LLM WAIT' : 'LLM IDLE';
    const socketBadgeColor = websocketLive ? 'success' : websocketReconnecting ? 'warning' : 'error';
    if (!shouldRenderOverlay) {
        return null;
    }
    return (_jsx("div", { "data-testid": "llm-runtime-overlay", className: cn('pointer-events-none fixed right-3 z-40 w-[min(94vw,420px)] sm:right-4', compactFactoryMode
            ? 'bottom-3 sm:bottom-4 sm:w-[320px]'
            : roleWorkspaceMode
                ? 'bottom-28 sm:bottom-28 sm:w-[360px]'
                : 'bottom-16 sm:bottom-6 sm:w-[400px]'), children: _jsxs("div", { className: "soft-panel pointer-events-auto rounded-xl backdrop-blur-xl", children: [_jsxs("button", { type: "button", onClick: () => setExpanded((prev) => !prev), className: "group flex w-full items-center gap-2 rounded-t-xl px-3 py-2.5 text-left transition-all hover:bg-white/70", children: [_jsx("div", { className: "soft-raised flex items-center justify-center rounded-lg p-1.5", children: (running || websocketReconnecting) ? (_jsx(Loader2, { className: "size-4 animate-spin text-gold" })) : (_jsx(Activity, { className: "size-4 text-gold" })) }), _jsxs("div", { className: "min-w-0 flex-1", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx("span", { className: "text-xs font-bold tracking-wider text-text-main", children: "LLM Runtime" }), _jsx("span", { className: "rounded-full border border-amber-500/20 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-gold", children: viewLabel(activeView) }), pmRunning && (_jsx("span", { className: "rounded border border-green-500/30 bg-green-500/10 px-1 py-0.5 text-[8px] font-bold text-green-300", children: "PM ACTIVE" })), directorRunning && (_jsx("span", { className: "rounded border border-cyan-500/30 bg-cyan-500/10 px-1 py-0.5 text-[8px] font-bold text-cyan-300", children: "DIR ACTIVE" }))] }), _jsx("div", { className: "truncate text-[11px] font-medium text-text-muted", children: headline })] }), expanded ? (_jsx(ChevronUp, { className: "size-4 text-gold transition-colors" })) : (_jsx(ChevronDown, { className: "size-4 text-text-muted transition-colors group-hover:text-text-main" }))] }), _jsx("div", { "data-testid": "llm-runtime-overlay-details", className: cn('grid transition-all duration-300', expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'), children: _jsx("div", { className: "overflow-hidden", children: _jsxs("div", { className: "border-t border-border px-3 py-3", children: [_jsxs("div", { className: "mb-3 flex flex-wrap items-center gap-2", children: [_jsx(StatusBadge, { color: pmRunning ? 'success' : 'default', variant: "dot", pulse: pmRunning, children: _jsx("span", { className: "font-mono text-[10px]", children: pmRunning ? 'PM RUN' : 'PM IDLE' }) }), _jsx(StatusBadge, { color: directorRunning ? 'info' : 'default', variant: "dot", pulse: directorRunning, children: _jsx("span", { className: "font-mono text-[10px]", children: directorRunning ? 'DIR RUN' : 'DIR IDLE' }) }), _jsx(StatusBadge, { color: llmBadgeColor, variant: "dot", pulse: llmStateToken === 'ready', children: _jsx("span", { className: "font-mono text-[10px]", children: llmBadgeLabel }) }), _jsx(StatusBadge, { color: socketBadgeColor, variant: "dot", pulse: websocketLive, children: _jsx("span", { className: "font-mono text-[9px]", children: websocketLive ? 'WS LIVE' : websocketReconnecting ? 'WS RECONNECT' : 'WS OFFLINE' }) })] }), _jsxs("div", { className: "soft-panel-subtle mb-3 rounded-xl px-3 py-2.5", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Radar, { className: "size-4 text-gold" }), _jsx("span", { className: "text-xs font-bold tracking-wider text-text-main", children: "\u5F53\u524D\u9636\u6BB5" })] }), _jsx("span", { className: "rounded border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-xs font-bold text-gold", children: phaseLabel })] }), _jsxs("div", { className: "mt-2 flex items-center justify-between text-[10px] text-text-muted", children: [_jsxs("span", { className: "flex items-center gap-1", children: [_jsx(Bot, { className: "size-3 text-cyan-400" }), "LLM \u66F4\u65B0"] }), _jsx("span", { className: "font-mono", children: toRelativeTime(effectiveUpdateTime) })] }), qualityGateDisplay && (_jsxs("div", { className: "mt-1.5 flex items-center justify-between text-[10px]", children: [_jsxs("span", { className: "flex items-center gap-1 text-text-muted", children: [_jsx(Sparkles, { className: "size-3 text-accent" }), "\u8D28\u91CF\u95E8\u63A7"] }), _jsx("span", { className: cn('font-mono font-bold', overlayQualityGateToneClass(qualityGateDisplay.tone)), children: qualityGateDisplay.label })] })), latestFileEdit && (_jsxs("div", { className: "mt-1.5 flex items-center justify-between gap-2 text-[10px]", "data-testid": "llm-runtime-file-edit", children: [_jsxs("span", { className: "flex min-w-0 items-center gap-1 text-text-muted", children: [_jsx(FileCode, { className: "size-3 text-emerald-300" }), _jsx("span", { className: "truncate", children: latestFileEdit.filePath })] }), _jsx("span", { className: "shrink-0 font-mono text-emerald-300", children: latestFileEdit.operation })] }))] }), visibleRequiredRoles.length > 0 && (_jsxs("div", { className: "mb-2 flex items-center gap-2 text-[10px] text-text-muted", children: [_jsx(Bot, { className: "size-3.5 text-cyan-300" }), _jsxs("span", { className: "truncate", children: ["required: ", visibleRequiredRoles.join(', ')] })] })), visibleBlockedRoles.length > 0 && (_jsxs("div", { className: "mb-2 rounded-lg border border-red-500/30 bg-red-500/10 px-2 py-1 text-[10px] text-red-200", children: ["blocked: ", visibleBlockedRoles.join(', ')] })), _jsxs("div", { className: "soft-panel-subtle rounded-xl p-2", children: [_jsxs("div", { className: "mb-2 flex items-center justify-between text-[11px] text-text-muted", children: [_jsxs("span", { className: "flex items-center gap-1.5", children: [websocketLive ? _jsx(Wifi, { className: "size-3.5 text-emerald-300" }) : _jsx(WifiOff, { className: "size-3.5 text-amber-300" }), "\u5B9E\u65F6\u63A8\u7406\u6D41"] }), _jsxs("span", { className: "text-text-dim", children: [recentSteps.length, " events"] })] }), _jsxs("div", { className: "space-y-1.5", children: [recentSteps.length === 0 && (_jsx("div", { className: "soft-inset rounded-lg px-2 py-1.5 text-[10px] text-text-dim italic", children: "\u7B49\u5F85 LLM \u4E8B\u4EF6\u6D41..." })), recentSteps.map((step, idx) => {
                                                    const isLatest = idx === 0;
                                                    const isThinking = getStreamEvent(step) === 'thinking_chunk';
                                                    const isToolCall = getStreamEvent(step) === 'tool_call';
                                                    return (_jsxs("div", { className: cn('relative rounded-lg border px-2 py-1.5 transition-all', streamEventStyle(step), isLatest && 'ring-1 ring-cyan-400/20'), children: [_jsxs("div", { className: "mb-0.5 flex items-center justify-between gap-2", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-1.5", children: [streamEventIcon(step), _jsx("span", { className: cn('truncate text-[10px] font-medium', isThinking ? 'text-gold' : isToolCall ? 'text-status-success' : 'text-accent-text'), children: step.source }), streamEventLabel(step) && (_jsx("span", { className: cn('shrink-0 rounded border px-1 py-0.5 text-[8px] font-bold tracking-wider', getStreamEvent(step) === 'thinking_chunk' && 'border-amber-500/30 bg-amber-500/15 text-gold', getStreamEvent(step) === 'content_chunk' && 'border-teal-600/25 bg-teal-600/10 text-accent-text', getStreamEvent(step) === 'tool_call' && 'border-green-600/25 bg-green-600/10 text-status-success', getStreamEvent(step) === 'tool_result' && 'border-emerald-600/25 bg-emerald-600/10 text-status-success'), children: streamEventLabel(step) }))] }), _jsx("span", { className: "shrink-0 text-[9px] text-text-dim", children: toRelativeTime(step.timestamp) })] }), _jsx(TypingMessage, { text: displayLogMessage(step), animate: isTypingStreamEvent(step) }), step.details && !isStructuredRuntimeText(step.details) && (_jsx("div", { className: "mt-0.5 font-mono text-[9px] text-text-muted", children: step.details }))] }, step.id));
                                                })] })] })] }) }) })] }) }));
}
