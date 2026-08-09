import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, AlertTriangle, Archive, Loader2, MessageSquare, RefreshCw, ShieldCheck } from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import { listRoleSessionArtifactEvidence, listRoleSessionAuditEvidence, listRoleSessionMessageEvidence, } from '@/services/roleSessionService';
const EMPTY_STATE = {
    loading: false,
    messages: [],
    artifacts: [],
    auditEvents: [],
    messageTotal: 0,
    artifactTotal: 0,
    auditTotal: 0,
    errors: {},
};
const TONE_CLASSES = {
    amber: {
        border: 'border-amber-500/[0.15]',
        bg: 'bg-amber-500/5',
        text: 'text-amber-100',
        accent: 'text-amber-300',
        badge: 'border-amber-500/20 bg-amber-500/10 text-amber-200',
        hover: 'hover:bg-amber-500/10 hover:text-amber-100',
    },
    emerald: {
        border: 'border-emerald-500/[0.15]',
        bg: 'bg-emerald-500/5',
        text: 'text-emerald-100',
        accent: 'text-emerald-300',
        badge: 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200',
        hover: 'hover:bg-emerald-500/10 hover:text-emerald-100',
    },
    cyan: {
        border: 'border-cyan-500/[0.15]',
        bg: 'bg-cyan-500/5',
        text: 'text-cyan-100',
        accent: 'text-cyan-300',
        badge: 'border-cyan-500/20 bg-cyan-500/10 text-cyan-200',
        hover: 'hover:bg-cyan-500/10 hover:text-cyan-100',
    },
    purple: {
        border: 'border-purple-500/[0.15]',
        bg: 'bg-purple-500/5',
        text: 'text-purple-100',
        accent: 'text-purple-300',
        badge: 'border-purple-500/20 bg-purple-500/10 text-purple-200',
        hover: 'hover:bg-purple-500/10 hover:text-purple-100',
    },
    rose: {
        border: 'border-rose-500/[0.15]',
        bg: 'bg-rose-500/5',
        text: 'text-rose-100',
        accent: 'text-rose-300',
        badge: 'border-rose-500/20 bg-rose-500/10 text-rose-200',
        hover: 'hover:bg-rose-500/10 hover:text-rose-100',
    },
    indigo: {
        border: 'border-indigo-500/[0.15]',
        bg: 'bg-indigo-500/5',
        text: 'text-indigo-100',
        accent: 'text-indigo-300',
        badge: 'border-indigo-500/20 bg-indigo-500/10 text-indigo-200',
        hover: 'hover:bg-indigo-500/10 hover:text-indigo-100',
    },
}, satisfies, Record;
() => ;
() => ;
 > ;
export function RoleSessionEvidencePanel({ sessionId, tone }) {
    const [state, setState] = useState(EMPTY_STATE);
    const requestIdRef = useRef(0);
    const styles = TONE_CLASSES[tone];
    const loadEvidence = useCallback(async () => {
        const requestId = requestIdRef.current + 1;
        requestIdRef.current = requestId;
        const token = String(sessionId || '').trim();
        if (!token) {
            setState(EMPTY_STATE);
            return;
        }
        setState((current) => ({
            ...current,
            loading: true,
            errors: {},
        }));
        try {
            const [messagesResult, artifactsResult, auditResult] = await Promise.all([
                listRoleSessionMessageEvidence(token, { limit: 5, offset: 0 }),
                listRoleSessionArtifactEvidence(token),
                listRoleSessionAuditEvidence(token, { limit: 5, offset: 0 }),
            ]);
            if (requestIdRef.current !== requestId) {
                return;
            }
            const errors = {};
            if (!messagesResult.ok) {
                errors.messages = messagesResult.error || 'messages unavailable';
            }
            if (!artifactsResult.ok) {
                errors.artifacts = artifactsResult.error || 'artifacts unavailable';
            }
            if (!auditResult.ok) {
                errors.audit = auditResult.error || 'audit unavailable';
            }
            setState({
                loading: false,
                messages: messagesResult.ok ? messagesResult.data?.items ?? [] : [],
                artifacts: artifactsResult.ok ? artifactsResult.data?.items ?? [] : [],
                auditEvents: auditResult.ok ? auditResult.data?.items ?? [] : [],
                messageTotal: messagesResult.ok ? messagesResult.data?.total ?? 0 : 0,
                artifactTotal: artifactsResult.ok ? artifactsResult.data?.total ?? 0 : 0,
                auditTotal: auditResult.ok ? auditResult.data?.total ?? 0 : 0,
                errors,
            });
        }
        catch (err) {
            if (requestIdRef.current !== requestId) {
                return;
            }
            const error = err instanceof Error ? err.message : 'RoleSession evidence unavailable';
            setState({
                loading: false,
                messages: [],
                artifacts: [],
                auditEvents: [],
                messageTotal: 0,
                artifactTotal: 0,
                auditTotal: 0,
                errors: {
                    messages: error,
                    artifacts: error,
                    audit: error,
                },
            });
        }
    }, [sessionId]);
    useEffect(() => {
        void loadEvidence();
        return () => {
            requestIdRef.current += 1;
        };
    }, [loadEvidence]);
    const endpointBase = sessionId ? `/v2/roles/sessions/${sessionId}` : '/v2/roles/sessions/{session_id}';
    const latestMessage = useMemo(() => formatLatestMessage(state.messages[0]), [state.messages]);
    const latestArtifact = useMemo(() => formatLatestArtifact(state.artifacts[0]), [state.artifacts]);
    const latestAudit = useMemo(() => formatLatestAudit(state.auditEvents[0]), [state.auditEvents]);
    return (_jsx("section", { className: cn('min-w-0 overflow-hidden border-b px-4 py-2 text-[11px]', styles.border, styles.bg), "data-testid": "role-session-evidence-panel", children: _jsxs("div", { className: "grid min-w-0 gap-2", children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center gap-2", children: [_jsxs("div", { className: cn('flex shrink-0 items-center gap-1.5 font-medium', styles.text), children: [_jsx(ShieldCheck, { className: cn('h-3.5 w-3.5', styles.accent) }), "RoleSession \u8BC1\u636E"] }), _jsx("span", { className: "shrink-0 rounded border border-white/10 bg-slate-950/65 px-1.5 py-0.5 text-[9px] font-medium text-slate-500", title: endpointBase, "data-endpoint": endpointBase, "data-testid": "role-session-evidence-endpoint", children: "API" }), _jsx("button", { type: "button", onClick: () => { void loadEvidence(); }, disabled: !sessionId || state.loading, title: "\u5237\u65B0 RoleSession \u8BC1\u636E", "data-testid": "role-session-evidence-refresh", className: cn('inline-flex h-7 w-7 shrink-0 cursor-pointer items-center justify-center rounded text-slate-400 transition-colors disabled:cursor-not-allowed disabled:text-slate-600', styles.hover), children: state.loading ? (_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin" })) : (_jsx(RefreshCw, { className: "h-3.5 w-3.5" })) })] }), !sessionId ? (_jsx("span", { className: "text-slate-500", "data-testid": "role-session-evidence-empty", children: "\u7B49\u5F85\u4F1A\u8BDD" })) : (_jsxs("div", { className: "grid min-w-0 grid-cols-1 gap-2", children: [_jsx(EvidenceMetric, { icon: _jsx(MessageSquare, { className: cn('h-3.5 w-3.5', styles.accent) }), endpoint: `${endpointBase}/messages`, label: "messages", count: state.messageTotal, previewCount: state.messages.length, latest: latestMessage, error: state.errors.messages, badgeClass: styles.badge }), _jsx(EvidenceMetric, { icon: _jsx(Archive, { className: cn('h-3.5 w-3.5', styles.accent) }), endpoint: `${endpointBase}/artifacts`, label: "artifacts", count: state.artifactTotal, previewCount: state.artifacts.length, latest: latestArtifact, error: state.errors.artifacts, badgeClass: styles.badge }), _jsx(EvidenceMetric, { icon: _jsx(Activity, { className: cn('h-3.5 w-3.5', styles.accent) }), endpoint: `${endpointBase}/audit`, label: "audit", count: state.auditTotal, previewCount: state.auditEvents.length, latest: latestAudit, error: state.errors.audit, badgeClass: styles.badge })] }))] }) }));
}
function EvidenceMetric({ icon, endpoint, label, count, previewCount, latest, error, badgeClass, }) {
    const displayLatest = error || latest;
    const previewText = previewCount < count ? `预览 ${previewCount}` : null;
    return (_jsxs("div", { className: cn('flex min-w-0 max-w-full flex-wrap items-center gap-2 rounded border bg-slate-950/55 px-2 py-1', error ? 'border-rose-500/25 text-rose-200' : 'border-white/10'), title: `${endpoint} · ${displayLatest}`, "data-endpoint": endpoint, "data-testid": `role-session-evidence-${label}`, children: [icon, _jsx("span", { className: "font-mono text-[10px] text-slate-500", children: label }), _jsx("span", { className: cn('rounded border px-1.5 py-0.5 font-mono text-[10px]', badgeClass), children: count }), previewText ? _jsx("span", { className: "font-mono text-[10px] text-slate-500", children: previewText }) : null, error ? _jsx(AlertTriangle, { className: "h-3 w-3 shrink-0 text-rose-300" }) : null, _jsx("span", { className: cn('min-w-0 truncate', error ? 'text-rose-200' : 'text-slate-300'), "data-testid": error ? `role-session-evidence-${label}-error` : undefined, children: displayLatest })] }));
}
function formatLatestMessage(message) {
    if (!message)
        return 'none';
    const role = String(message.role || 'message').trim();
    const content = String(message.content || message.thinking || '').trim();
    return content ? `${role}: ${content}` : role;
}
function formatLatestArtifact(artifact) {
    if (!artifact)
        return 'none';
    const artifactType = String(artifact.type || 'artifact').trim();
    return artifact.id ? `${artifactType}: ${artifact.id}` : artifactType;
}
function formatLatestAudit(event) {
    if (!event)
        return 'none';
    const eventType = String(event.event_type || event.type || 'event').trim();
    const timestamp = String(event.timestamp || event.created_at || '').trim();
    return timestamp ? `${eventType} · ${timestamp}` : eventType;
}
