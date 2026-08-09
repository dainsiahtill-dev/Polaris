import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw, ShieldAlert } from 'lucide-react';
import { getChiefEngineerReleaseReadiness, listChiefEngineerADRs, listChiefEngineerPostMortems, listChiefEngineerRisks, listChiefEngineerTechDebt, listChiefEngineerTechRadar, } from '@/services/chiefEngineerService';
const RELEASE_DECISION_CLASS = {
    go: 'border-emerald-500/40 bg-emerald-950/50 text-emerald-200',
    conditional_go: 'border-amber-500/40 bg-amber-950/50 text-amber-200',
    no_go: 'border-red-500/50 bg-red-950/60 text-red-200',
};
const RELEASE_DECISION_LABEL = {
    go: 'GO',
    conditional_go: 'CONDITIONAL',
    no_go: 'NO-GO',
};
const RADAR_RING_CLASS = {
    adopt: 'bg-emerald-800/60 text-emerald-100',
    trial: 'bg-sky-800/60 text-sky-100',
    hold: 'bg-amber-800/60 text-amber-100',
    deprecated: 'bg-red-900/80 text-red-50',
};
const INCIDENT_SEVERITY_CLASS = {
    sev1: 'bg-red-900/80 text-red-50',
    sev2: 'bg-orange-700/60 text-orange-100',
    sev3: 'bg-amber-700/50 text-amber-100',
    sev4: 'bg-slate-700/60 text-slate-200',
};
const RISK_SEVERITY_CLASS = {
    low: 'bg-slate-700/60 text-slate-200',
    medium: 'bg-amber-700/50 text-amber-100',
    high: 'bg-orange-700/60 text-orange-100',
    critical: 'bg-red-700/60 text-red-100',
    blocker: 'bg-red-900/80 text-red-50',
};
const DEBT_SEVERITY_CLASS = {
    trivial: 'bg-slate-700/60 text-slate-200',
    minor: 'bg-sky-700/50 text-sky-100',
    major: 'bg-amber-700/60 text-amber-100',
    severe: 'bg-orange-700/60 text-orange-100',
    fatal: 'bg-red-900/80 text-red-50',
};
function SeverityBadge({ label, className }) {
    return (_jsx("span", { className: `shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${className}`, children: label }));
}
/**
 * Read-only Tier-1 governance view for the Chief Engineer (技术总监) surface.
 *
 * Renders the workspace Risk Register and Tech-Debt Ledger fetched from the
 * `/v2/chief-engineer/risks` and `/v2/chief-engineer/tech-debt` endpoints.
 * Mutations (register / status transitions) are intentionally out of scope
 * for Tier-1 — this panel is observe-only.
 */
export function ChiefEngineerGovernancePanel({ workspace }) {
    const [state, setState] = useState({
        loading: false,
        error: null,
        risks: [],
        techDebt: [],
        adrs: [],
        techRadar: [],
        postMortems: [],
        readiness: null,
    });
    const load = useCallback(async () => {
        setState((prev) => ({ ...prev, loading: true, error: null }));
        const [risksResult, debtResult, adrResult, radarResult, pmResult, readinessResult] = await Promise.all([
            listChiefEngineerRisks({}, workspace),
            listChiefEngineerTechDebt({}, workspace),
            listChiefEngineerADRs({}, workspace),
            listChiefEngineerTechRadar(undefined, workspace),
            listChiefEngineerPostMortems({}, workspace),
            getChiefEngineerReleaseReadiness({}, workspace),
        ]);
        if (!risksResult.ok || !risksResult.data) {
            setState((prev) => ({
                ...prev,
                loading: false,
                error: risksResult.error ?? 'Failed to load Chief Engineer risks',
            }));
            return;
        }
        if (!debtResult.ok || !debtResult.data) {
            setState((prev) => ({
                ...prev,
                loading: false,
                error: debtResult.error ?? 'Failed to load Chief Engineer tech debt',
            }));
            return;
        }
        if (!adrResult.ok || !adrResult.data) {
            setState((prev) => ({
                ...prev,
                loading: false,
                error: adrResult.error ?? 'Failed to load Chief Engineer ADRs',
            }));
            return;
        }
        if (!radarResult.ok || !radarResult.data) {
            setState((prev) => ({
                ...prev,
                loading: false,
                error: radarResult.error ?? 'Failed to load Chief Engineer tech radar',
            }));
            return;
        }
        if (!pmResult.ok || !pmResult.data) {
            setState((prev) => ({
                ...prev,
                loading: false,
                error: pmResult.error ?? 'Failed to load Chief Engineer post-mortems',
            }));
            return;
        }
        setState({
            loading: false,
            error: null,
            risks: risksResult.data.risks,
            techDebt: debtResult.data.tech_debt,
            adrs: adrResult.data.adrs,
            techRadar: radarResult.data.entries,
            postMortems: pmResult.data.post_mortems,
            // Release readiness is advisory — a failure here must not blank the panel.
            readiness: readinessResult.ok && readinessResult.data ? readinessResult.data.readiness : null,
        });
    }, [workspace]);
    useEffect(() => {
        void load();
    }, [load]);
    return (_jsxs("section", { className: "flex flex-col gap-3 rounded-lg border border-white/10 bg-slate-950/40 p-3", "data-testid": "ce-governance-panel", children: [_jsxs("header", { className: "flex items-center justify-between", children: [_jsxs("h3", { className: "flex items-center gap-1.5 text-sm font-semibold text-slate-200", children: [_jsx(ShieldAlert, { className: "h-4 w-4 text-amber-300", "aria-hidden": true }), "Governance"] }), _jsxs("button", { type: "button", onClick: () => void load(), disabled: state.loading, className: "flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/5 disabled:opacity-50", "data-testid": "ce-governance-refresh", children: [_jsx(RefreshCw, { className: `h-3 w-3 ${state.loading ? 'animate-spin' : ''}`, "aria-hidden": true }), "Refresh"] })] }), state.error ? (_jsxs("div", { className: "flex items-center gap-1.5 rounded border border-red-500/30 bg-red-950/40 px-2 py-1 text-[11px] text-red-200", "data-testid": "ce-governance-error", children: [_jsx(AlertTriangle, { className: "h-3.5 w-3.5", "aria-hidden": true }), state.error] })) : null, state.readiness ? (_jsxs("div", { className: `flex items-center gap-2 rounded border px-2.5 py-1.5 text-[11px] ${RELEASE_DECISION_CLASS[state.readiness.decision]}`, "data-testid": "ce-release-readiness", "data-decision": state.readiness.decision, children: [_jsxs("span", { className: "shrink-0 rounded bg-black/30 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide", children: ["Release ", RELEASE_DECISION_LABEL[state.readiness.decision]] }), _jsxs("span", { className: "min-w-0 flex-1 truncate", children: [state.readiness.blocker_count, " blocker(s), ", state.readiness.warning_count, " warning(s)", state.readiness.blockers.length > 0 ? ` — ${state.readiness.blockers[0]}` : ''] })] })) : null, _jsxs("div", { className: "grid gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5", children: [_jsxs("div", { children: [_jsxs("div", { className: "mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400", children: ["Risk Register (", state.risks.length, ")"] }), state.risks.length === 0 && !state.loading ? (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "ce-risks-empty", children: "No risks registered." })) : (_jsx("ul", { className: "flex flex-col gap-1", "data-testid": "ce-risks-list", children: state.risks.map((risk) => (_jsxs("li", { className: "flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200", children: [_jsx(SeverityBadge, { label: risk.severity, className: RISK_SEVERITY_CLASS[risk.severity] }), _jsx("span", { className: "min-w-0 flex-1 truncate", title: risk.title, children: risk.title }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-400", children: risk.status })] }, risk.risk_id))) }))] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400", children: ["Tech-Debt Ledger (", state.techDebt.length, ")"] }), state.techDebt.length === 0 && !state.loading ? (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "ce-tech-debt-empty", children: "No tech debt registered." })) : (_jsx("ul", { className: "flex flex-col gap-1", "data-testid": "ce-tech-debt-list", children: state.techDebt.map((debt) => (_jsxs("li", { className: "flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200", children: [_jsx(SeverityBadge, { label: debt.severity, className: DEBT_SEVERITY_CLASS[debt.severity] }), _jsx("span", { className: "min-w-0 flex-1 truncate", title: debt.title, children: debt.title }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-400", children: debt.status })] }, debt.debt_id))) }))] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400", children: ["Decision Log (", state.adrs.length, ")"] }), state.adrs.length === 0 && !state.loading ? (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "ce-adrs-empty", children: "No decisions recorded." })) : (_jsx("ul", { className: "flex flex-col gap-1", "data-testid": "ce-adrs-list", children: state.adrs.map((adr) => (_jsxs("li", { className: "flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200", children: [_jsx("span", { className: "min-w-0 flex-1 truncate", title: adr.title, children: adr.title }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-400", children: adr.status })] }, adr.adr_id))) }))] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400", children: ["Tech Radar (", state.techRadar.length, ")"] }), state.techRadar.length === 0 && !state.loading ? (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "ce-tech-radar-empty", children: "No tech radar entries." })) : (_jsx("ul", { className: "flex flex-col gap-1", "data-testid": "ce-tech-radar-list", children: state.techRadar.map((entry) => (_jsxs("li", { className: "flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200", children: [_jsx(SeverityBadge, { label: entry.ring, className: RADAR_RING_CLASS[entry.ring] }), _jsx("span", { className: "min-w-0 flex-1 truncate", title: entry.library, children: entry.library })] }, entry.entry_id))) }))] }), _jsxs("div", { children: [_jsxs("div", { className: "mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400", children: ["Post-Mortems (", state.postMortems.length, ")"] }), state.postMortems.length === 0 && !state.loading ? (_jsx("div", { className: "text-[11px] text-slate-500", "data-testid": "ce-post-mortems-empty", children: "No post-mortems recorded." })) : (_jsx("ul", { className: "flex flex-col gap-1", "data-testid": "ce-post-mortems-list", children: state.postMortems.map((pm) => (_jsxs("li", { className: "flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200", children: [_jsx(SeverityBadge, { label: pm.severity, className: INCIDENT_SEVERITY_CLASS[pm.severity] }), _jsx("span", { className: "min-w-0 flex-1 truncate", title: pm.title, children: pm.title }), _jsx("span", { className: "shrink-0 text-[10px] text-slate-400", children: pm.status })] }, pm.incident_id))) }))] })] })] }));
}
