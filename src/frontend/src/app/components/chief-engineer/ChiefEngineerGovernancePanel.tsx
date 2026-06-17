import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, RefreshCw, ShieldAlert } from 'lucide-react';
import {
  getChiefEngineerReleaseReadiness,
  listChiefEngineerADRs,
  listChiefEngineerPostMortems,
  listChiefEngineerRisks,
  listChiefEngineerTechDebt,
  listChiefEngineerTechRadar,
  type ADRRecord,
  type IncidentSeverity,
  type PostMortemRecord,
  type ReleaseDecision,
  type ReleaseReadiness,
  type RiskRecord,
  type RiskSeverity,
  type TechDebtRecord,
  type TechDebtSeverity,
  type TechRadarEntry,
  type TechRadarRing,
} from '@/services/chiefEngineerService';

interface ChiefEngineerGovernancePanelProps {
  workspace: string;
}

interface GovernanceState {
  loading: boolean;
  error: string | null;
  risks: RiskRecord[];
  techDebt: TechDebtRecord[];
  adrs: ADRRecord[];
  techRadar: TechRadarEntry[];
  postMortems: PostMortemRecord[];
  readiness: ReleaseReadiness | null;
}

const RELEASE_DECISION_CLASS: Record<ReleaseDecision, string> = {
  go: 'border-emerald-500/40 bg-emerald-950/50 text-emerald-200',
  conditional_go: 'border-amber-500/40 bg-amber-950/50 text-amber-200',
  no_go: 'border-red-500/50 bg-red-950/60 text-red-200',
};

const RELEASE_DECISION_LABEL: Record<ReleaseDecision, string> = {
  go: 'GO',
  conditional_go: 'CONDITIONAL',
  no_go: 'NO-GO',
};

const RADAR_RING_CLASS: Record<TechRadarRing, string> = {
  adopt: 'bg-emerald-800/60 text-emerald-100',
  trial: 'bg-sky-800/60 text-sky-100',
  hold: 'bg-amber-800/60 text-amber-100',
  deprecated: 'bg-red-900/80 text-red-50',
};

const INCIDENT_SEVERITY_CLASS: Record<IncidentSeverity, string> = {
  sev1: 'bg-red-900/80 text-red-50',
  sev2: 'bg-orange-700/60 text-orange-100',
  sev3: 'bg-amber-700/50 text-amber-100',
  sev4: 'bg-slate-700/60 text-slate-200',
};

const RISK_SEVERITY_CLASS: Record<RiskSeverity, string> = {
  low: 'bg-slate-700/60 text-slate-200',
  medium: 'bg-amber-700/50 text-amber-100',
  high: 'bg-orange-700/60 text-orange-100',
  critical: 'bg-red-700/60 text-red-100',
  blocker: 'bg-red-900/80 text-red-50',
};

const DEBT_SEVERITY_CLASS: Record<TechDebtSeverity, string> = {
  trivial: 'bg-slate-700/60 text-slate-200',
  minor: 'bg-sky-700/50 text-sky-100',
  major: 'bg-amber-700/60 text-amber-100',
  severe: 'bg-orange-700/60 text-orange-100',
  fatal: 'bg-red-900/80 text-red-50',
};

function SeverityBadge({ label, className }: { label: string; className: string }) {
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${className}`}>
      {label}
    </span>
  );
}

/**
 * Read-only Tier-1 governance view for the Chief Engineer (技术总监) surface.
 *
 * Renders the workspace Risk Register and Tech-Debt Ledger fetched from the
 * `/v2/chief-engineer/risks` and `/v2/chief-engineer/tech-debt` endpoints.
 * Mutations (register / status transitions) are intentionally out of scope
 * for Tier-1 — this panel is observe-only.
 */
export function ChiefEngineerGovernancePanel({ workspace }: ChiefEngineerGovernancePanelProps) {
  const [state, setState] = useState<GovernanceState>({
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

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-white/10 bg-slate-950/40 p-3" data-testid="ce-governance-panel">
      <header className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-200">
          <ShieldAlert className="h-4 w-4 text-amber-300" aria-hidden />
          Governance
        </h3>
        <button
          type="button"
          onClick={() => void load()}
          disabled={state.loading}
          className="flex items-center gap-1 rounded border border-white/10 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-white/5 disabled:opacity-50"
          data-testid="ce-governance-refresh"
        >
          <RefreshCw className={`h-3 w-3 ${state.loading ? 'animate-spin' : ''}`} aria-hidden />
          Refresh
        </button>
      </header>

      {state.error ? (
        <div
          className="flex items-center gap-1.5 rounded border border-red-500/30 bg-red-950/40 px-2 py-1 text-[11px] text-red-200"
          data-testid="ce-governance-error"
        >
          <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
          {state.error}
        </div>
      ) : null}

      {state.readiness ? (
        <div
          className={`flex items-center gap-2 rounded border px-2.5 py-1.5 text-[11px] ${RELEASE_DECISION_CLASS[state.readiness.decision]}`}
          data-testid="ce-release-readiness"
          data-decision={state.readiness.decision}
        >
          <span className="shrink-0 rounded bg-black/30 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide">
            Release {RELEASE_DECISION_LABEL[state.readiness.decision]}
          </span>
          <span className="min-w-0 flex-1 truncate">
            {state.readiness.blocker_count} blocker(s), {state.readiness.warning_count} warning(s)
            {state.readiness.blockers.length > 0 ? ` — ${state.readiness.blockers[0]}` : ''}
          </span>
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Risk Register ({state.risks.length})
          </div>
          {state.risks.length === 0 && !state.loading ? (
            <div className="text-[11px] text-slate-500" data-testid="ce-risks-empty">
              No risks registered.
            </div>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="ce-risks-list">
              {state.risks.map((risk) => (
                <li
                  key={risk.risk_id}
                  className="flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200"
                >
                  <SeverityBadge label={risk.severity} className={RISK_SEVERITY_CLASS[risk.severity]} />
                  <span className="min-w-0 flex-1 truncate" title={risk.title}>
                    {risk.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-400">{risk.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Tech-Debt Ledger ({state.techDebt.length})
          </div>
          {state.techDebt.length === 0 && !state.loading ? (
            <div className="text-[11px] text-slate-500" data-testid="ce-tech-debt-empty">
              No tech debt registered.
            </div>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="ce-tech-debt-list">
              {state.techDebt.map((debt) => (
                <li
                  key={debt.debt_id}
                  className="flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200"
                >
                  <SeverityBadge label={debt.severity} className={DEBT_SEVERITY_CLASS[debt.severity]} />
                  <span className="min-w-0 flex-1 truncate" title={debt.title}>
                    {debt.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-400">{debt.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Decision Log ({state.adrs.length})
          </div>
          {state.adrs.length === 0 && !state.loading ? (
            <div className="text-[11px] text-slate-500" data-testid="ce-adrs-empty">
              No decisions recorded.
            </div>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="ce-adrs-list">
              {state.adrs.map((adr) => (
                <li
                  key={adr.adr_id}
                  className="flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200"
                >
                  <span className="min-w-0 flex-1 truncate" title={adr.title}>
                    {adr.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-400">{adr.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Tech Radar ({state.techRadar.length})
          </div>
          {state.techRadar.length === 0 && !state.loading ? (
            <div className="text-[11px] text-slate-500" data-testid="ce-tech-radar-empty">
              No tech radar entries.
            </div>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="ce-tech-radar-list">
              {state.techRadar.map((entry) => (
                <li
                  key={entry.entry_id}
                  className="flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200"
                >
                  <SeverityBadge label={entry.ring} className={RADAR_RING_CLASS[entry.ring]} />
                  <span className="min-w-0 flex-1 truncate" title={entry.library}>
                    {entry.library}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Post-Mortems ({state.postMortems.length})
          </div>
          {state.postMortems.length === 0 && !state.loading ? (
            <div className="text-[11px] text-slate-500" data-testid="ce-post-mortems-empty">
              No post-mortems recorded.
            </div>
          ) : (
            <ul className="flex flex-col gap-1" data-testid="ce-post-mortems-list">
              {state.postMortems.map((pm) => (
                <li
                  key={pm.incident_id}
                  className="flex items-start gap-1.5 rounded bg-slate-900/60 px-1.5 py-1 text-[11px] text-slate-200"
                >
                  <SeverityBadge label={pm.severity} className={INCIDENT_SEVERITY_CLASS[pm.severity]} />
                  <span className="min-w-0 flex-1 truncate" title={pm.title}>
                    {pm.title}
                  </span>
                  <span className="shrink-0 text-[10px] text-slate-400">{pm.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
