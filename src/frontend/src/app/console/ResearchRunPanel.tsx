import { useRef, useState } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { getFixedResearchPlan, getResearchRuns, RESEARCH_PAGE_SIZE, type ResearchRunPage } from '@/services/engineeringResearchService';
import type { EngineeringResearchPlan, ResearchIdentity } from '@/runtime/researchPlan';
import { normalizeRuntimeWorkspacePath } from '@/app/hooks/_runtimeEventFilter';
import { isRecord } from '@/app/hooks/runtimeParsing';
import { RUN_STATUS_LABELS, VERIFICATION_LABELS } from './format';

interface Props { workspace: string; sessionId: string; identity: ResearchIdentity; events: EngineeringEvent[]; }
interface PageView { lab: string | null; offset: number; data?: ResearchRunPage; error?: string; }
interface PlanView { sessionId: string; identity: ResearchIdentity; plan?: EngineeringResearchPlan; error?: string; }
const counts = { matched: '引用匹配', changed: '内容变化', needs_recheck: '需复核', unproven: '未证', unpublished: '尚未发表' };
interface ObservedExperiment { key: string; scope: string; state: keyof typeof counts; affected_by: string[]; next_question?: string }
function observedExperiment(v: unknown): v is ObservedExperiment {
  return isRecord(v) && typeof v.key === 'string' && typeof v.scope === 'string'
    && typeof v.state === 'string' && Object.prototype.hasOwnProperty.call(counts, v.state)
    && Array.isArray(v.affected_by) && v.affected_by.every((dependency: unknown) => typeof dependency === 'string')
    && (v.next_question === undefined || typeof v.next_question === 'string');
}

export function ResearchRunPanel(props: Props) {
  const { workspace, sessionId, identity } = props;
  return <ResearchRunDetails key={JSON.stringify([workspace, sessionId, identity.project, identity.lab, identity.plan_sha256])} {...props} />;
}

function ResearchRunDetails({ workspace, sessionId, identity, events }: Props) {
  const [history, setHistory] = useState<PageView | null>(null);
  const [detail, setDetail] = useState<PlanView | null>(null);
  const latest = useRef({ page: 0, detail: 0 });

  const closePlan = () => { latest.current.detail++; setDetail(null); };
  const closeHistory = () => { latest.current.page++; setHistory(null); closePlan(); };
  const loadPage = async (lab: string | null, offset = 0) => {
    const version = ++latest.current.page;
    closePlan();
    setHistory({ lab, offset });
    const result = await getResearchRuns(workspace, identity.project, lab, offset);
    if (version === latest.current.page)
      setHistory(result.ok && result.data ? { lab, offset, data: result.data } : { lab, offset, error: result.error || '读取实验室记录失败。' });
  };
  const loadPlan = async (id: string, selected: ResearchIdentity) => {
    const version = ++latest.current.detail;
    setDetail({ sessionId: id, identity: selected });
    const result = await getFixedResearchPlan(workspace, id, selected);
    if (version === latest.current.detail)
      setDetail({ sessionId: id, identity: selected, ...(result.ok && result.data ? { plan: result.data } : { error: result.error || '读取固定计划失败。' }) });
  };

  let observation: EngineeringEvent | undefined;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.kind === 'research_evidence_observed' && e.session_id === sessionId
      && normalizeRuntimeWorkspacePath(e.workspace) === normalizeRuntimeWorkspacePath(workspace)
      && e.payload.project === identity.project && e.payload.lab === identity.lab && e.payload.plan_sha256 === identity.plan_sha256) {
      observation = e;
      break;
    }
  }
  const totals = observation?.payload.state_counts;
  const entries = observation?.payload.experiments;
  const experiments = Array.isArray(entries) ? entries.filter(observedExperiment) : [];
  const omitted = observation?.payload.omitted_experiments;
  const summary = totals && typeof totals === 'object' && !Array.isArray(totals)
    ? Object.entries(counts).flatMap(([key, label]) => {
      const count: unknown = (totals as Record<string, unknown>)[key];
      return typeof count === 'number' && Number.isSafeInteger(count) && count > 0 ? [`${label} ${count}`] : [];
    }) : [];

  return <section className="cx-card space-y-3 p-4" aria-label="多实验室研究" data-testid="research-run-panel" data-session-id={sessionId}>
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-base font-bold">多实验室研究</h2>
        <p className="mt-1 break-words text-sm">项目 {identity.project} · 实验室 {identity.lab}</p>
        <p className="mt-1 break-all text-xs text-[var(--cx-ink-dim)]">计划 SHA-256 <code>{identity.plan_sha256}</code></p>
        {observation && summary.length ? <p className="mt-1 text-xs text-[var(--cx-ink-dim)]" data-testid="research-observation" data-observation-seq={observation.seq}>证据观察 #{observation.seq} · {summary.join(' · ')}</p> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="cx-btn cx-btn--ghost" onClick={() => void loadPlan(sessionId, identity)}>查看固定计划</button>
        <button type="button" className="cx-btn cx-btn--ghost" onClick={() => void loadPage(identity.lab)}>本实验室历史</button>
        <button type="button" className="cx-btn cx-btn--ghost" onClick={() => void loadPage(null)}>项目实验室</button>
      </div>
    </div>
    {experiments.length > 0 && <details className="border-t border-[var(--cx-line)] pt-2 text-sm">
      <summary className="cursor-pointer font-semibold">查看实验依赖</summary>
      <ul className="mt-1 divide-y divide-[var(--cx-line)]">
        {experiments.map((item) => <li key={item.key} className="space-y-1 break-words py-2">
          <p><code>{item.key}</code> · {counts[item.state]}</p>
          <p className="text-xs text-[var(--cx-ink-dim)]">{item.scope}</p>
          {item.affected_by.length > 0 && <p>受影响的依赖：{item.affected_by.join('、')}</p>}
          {item.next_question && <p>待核查：{item.next_question}</p>}
        </li>)}
      </ul>
      {typeof omitted === 'number' && omitted > 0 && <p className="text-xs text-[var(--cx-ink-dim)]">本次摘要另省略 {omitted} 项实验。</p>}
    </details>}
    {history && <section aria-label="实验室运行记录" className="space-y-2 border-t border-[var(--cx-line)] pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{history.lab ? `${history.lab} 的运行历史` : `${identity.project} 的实验室`}</h3>
        <button type="button" className="text-xs text-[var(--cx-ink-dim)] underline" onClick={closeHistory}>收起记录</button>
      </div>
      {history.error ? <p role="alert" className="text-sm text-red-700">{history.error}</p> : !history.data
        ? <p role="status" className="text-sm">正在读取记录…</p>
        : <>
          {!history.data.rows.length && <p className="text-sm text-[var(--cx-ink-dim)]">此页没有研究运行。</p>}
          {history.data.rows.map((row) => <div key={row.session_id} className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--cx-line)] py-2 text-sm">
            <div className="min-w-0">
              <p className="break-words font-semibold">{row.lab} · {RUN_STATUS_LABELS[row.status]} · 验证{VERIFICATION_LABELS[row.verification]}</p>
              <p className="break-all text-xs text-[var(--cx-ink-dim)]"><time>{new Date(row.created_at * 1000).toLocaleString()}</time> · <code>{row.session_id}</code></p>
            </div>
            <div className="flex flex-wrap gap-3">
              {!history.lab && <button type="button" className="underline" aria-label={`查看 ${row.lab} 的历史`} onClick={() => void loadPage(row.lab)}>查看历史</button>}
              <button type="button" className="underline" aria-label={`查看 ${row.session_id} 的固定计划`}
                onClick={() => void loadPlan(row.session_id, { project: identity.project, lab: row.lab, plan_sha256: row.plan_sha256 })}>查看计划</button>
            </div>
          </div>)}
          {(history.offset > 0 || history.data.nextOffset !== null) && <div className="flex items-center gap-3 text-xs">
            <button type="button" disabled={history.offset === 0} className="cx-btn cx-btn--ghost" onClick={() => void loadPage(history.lab, Math.max(0, history.offset - RESEARCH_PAGE_SIZE))}>上一页</button>
            <span>第 {Math.floor(history.offset / RESEARCH_PAGE_SIZE) + 1} 页</span>
            <button type="button" disabled={history.data.nextOffset === null} className="cx-btn cx-btn--ghost" onClick={() => { if (history.data?.nextOffset != null) void loadPage(history.lab, history.data.nextOffset); }}>下一页</button>
          </div>}
        </>}
    </section>}
    {detail && <section aria-label="固定研究计划" className="space-y-2 border-t border-[var(--cx-line)] pt-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">固定研究计划</h3>
        <button type="button" className="text-xs underline" onClick={closePlan}>收起计划</button>
      </div>
      <p className="break-all text-xs">实验室 {detail.identity.lab} · 运行 <code>{detail.sessionId}</code></p>
      <p className="break-all text-xs">计划 SHA-256 <code>{detail.identity.plan_sha256}</code></p>
      {detail.error ? <p role="alert" className="text-sm text-red-700">{detail.error}</p> : detail.plan
        ? <pre className="cx-code max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{JSON.stringify(detail.plan, null, 2)}</pre>
        : <p role="status" className="text-sm">正在读取计划…</p>}
    </section>}
  </section>;
}
