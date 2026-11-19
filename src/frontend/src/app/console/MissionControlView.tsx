import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringEvent, EngineeringRole } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { ContextViewerModal } from '@/app/components/contextos/ContextViewerModal';
import { BrowserQaCard } from './BrowserQaCard';
import { ContextRefCard } from './ContextRefCard';
import { GoalComposer } from './GoalComposer';
import { ROLE_LABELS } from './format';
import { RoleTracks } from './RoleTracks';
import { RunHero } from './RunHero';
import { StageGateBoard } from './StageGateBoard';
import { StatusDock } from './StatusDock';
import { ToolTimeline } from './ToolTimeline';
import { VerdictBoard } from './VerdictBoard';
import { IconContext, IconFlag, IconInbox, IconMonitor, IconPlus, IconRefresh } from './icons';

function timelineEvents(events: EngineeringEvent[]): EngineeringEvent[] {
  return events.filter((event) => !['message', 'run_updated'].includes(event.kind));
}

/** The Polaris Mission Control surface for one engineering workspace. */
export function MissionControlView({ workspace, binding, role, onRoleChange }: {
  workspace: string;
  binding: EngineeringRunBinding;
  role: EngineeringRole;
  onRoleChange: (role: EngineeringRole) => void;
}): ReactElement {
  const [goal, setGoal] = useState<string | null>(null);
  const [selectedContext, setSelectedContext] = useState<{ ref: string; role: string } | null>(null);
  const projection = binding.projection;
  const run = projection.snapshot;
  const events = useMemo(() => timelineEvents(projection.events), [projection.events]);
  const startedAt = useMemo(() => {
    for (let index = 0; index < events.length; index += 1) {
      if (events[index].created_at) return events[index].created_at;
    }
    return undefined;
  }, [events]);
  const verificationCommands = run?.verification_commands ?? run?.required_checks ?? [];
  const pendingSnapshot = Boolean(projection.error || projection.snapshotRequiredSeq);
  const connTone = binding.connected ? 'ok' : binding.reconnecting ? 'warn' : 'bad';
  const connText = binding.connected
    ? `实时连接已建立${binding.connectionError ? ` · ${binding.connectionError}` : ''}`
    : binding.reconnecting
      ? '正在重新连接；当前显示最后快照，恢复后自动补齐事件'
      : '实时连接已断开；当前显示最后快照，等待重连或手动刷新';
  const openContext = (ref: string, eventRole: string) => setSelectedContext({ ref, role: eventRole });
  const focusComposer = () => {
    const input = document.getElementById('engineering-instruction');
    input?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    input?.focus();
  };
  const startWithGoal = async (options: Parameters<EngineeringRunBinding['start']>[0]) => {
    const ok = await binding.start(options);
    if (ok) setGoal(options.instruction);
    return ok;
  };
  const composerBinding = { ...binding, start: startWithGoal };
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="engineering-run-workspace">
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 cx-scroll">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="cx-eyebrow">Engineering v2 · {ROLE_LABELS[role]}</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight">运行控制台 <span className="ml-2 align-middle text-sm font-medium text-[var(--cx-ink-faint)]">Mission Control</span></h1>
          </div>
          <button type="button" onClick={focusComposer} className="cx-btn cx-btn--primary"><IconPlus className="h-4 w-4" />新建运行</button>
        </div>
        <RunHero goal={goal ?? (run?.text ? run.text.split('\n')[0] : null)} sessionId={run?.session_id} workflow={run?.workflow} status={run?.status} roles={run?.roles} startedAtMs={startedAt} nowMs={Date.now()} />
        <div className={`cx-conn cx-conn--${connTone} flex items-center gap-2.5 px-3.5 py-2`} role="status">
          <span className={`cx-dot ${binding.connected ? 'cx-dot--ok' : binding.reconnecting ? 'cx-dot--warn' : 'cx-dot--bad'}`} />
          <p className="text-[var(--cx-ink-dim)]">{connText}</p>
          {binding.projection.historyWindowed ? <p className="text-[var(--cx-ink-faint)]">· 仅展示最近记录窗口</p> : null}
        </div>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
          <section className="cx-card cx-enter cx-enter-2 self-start xl:col-span-3">
            <div className="cx-card-head">
              <h2 className="cx-card-title"><IconMonitor className="h-4.5 w-4.5 text-[var(--cx-green)]" />角色轨道</h2>
            </div>
            <div className="p-3.5">
              <RoleTracks roles={run?.roles ?? { pm: 'not_called', chief_engineer: 'not_called', director: 'not_called', qa: 'not_called' }} />
            </div>
            <div className="cx-card-head border-t border-[var(--cx-line)]">
              <h2 className="cx-card-title"><IconContext className="h-4.5 w-4.5 text-[var(--cx-green)]" />ContextOS</h2>
            </div>
            <div className="p-3.5">
              <ContextRefCard events={events} onOpen={(ref) => openContext(ref, role)} />
            </div>
          </section>
          <section className="cx-card cx-enter cx-enter-3 flex min-h-[26rem] flex-col xl:col-span-5">
            <div className="cx-card-head">
              <h2 className="cx-card-title"><IconInbox className="h-4.5 w-4.5 text-[var(--cx-green)]" />工具调用时间线</h2>
              <span className="cx-pill cx-pill--idle">{events.length} 条收据</span>
            </div>
            <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3.5 cx-scroll">
              <ToolTimeline events={events} onOpenContext={openContext} emptyHint={binding.loading ? '正在读取快照…' : '尚无可展示的运行记录'} />
            </div>
          </section>
          <div className="space-y-4 xl:col-span-4">
            <section className="cx-card cx-enter cx-enter-4">
              <div className="cx-card-head">
                <h2 className="cx-card-title"><IconFlag className="h-4.5 w-4.5 text-[var(--cx-green)]" />阶段门禁</h2>
              </div>
              <div className="space-y-2.5 p-3.5">
                <StageGateBoard events={projection.events} commands={verificationCommands} checks={run?.checks ?? []} />
              </div>
            </section>
            <section className="cx-enter cx-enter-5">
              <BrowserQaCard checks={run?.checks ?? []} />
            </section>
            <section className="cx-card cx-enter cx-enter-5">
              <div className="cx-card-head">
                <h2 className="cx-card-title">判定 · VERDICT</h2>
              </div>
              <div className="space-y-2.5 p-3.5">
                <VerdictBoard
                  status={run?.status}
                  verification={run?.verification}
                  qaVerdict={run?.qa_verdict}
                  requireQa={run?.require_qa}
                  projectOutcome={run?.project_outcome}
                  acceptanceBasis={run?.acceptance_basis}
                  pendingSnapshot={pendingSnapshot}
                />
                {run?.truncated_fields?.length ? <p className="text-xs text-[var(--cx-amber)]">当前为截断预览，可刷新快照读取完整记录。</p> : null}
              </div>
            </section>
          </div>
        </div>
        <section className="cx-card cx-enter cx-enter-6">
          <div className="cx-card-head">
            <h2 className="cx-card-title">下达目标 · COMPOSE</h2>
            {run ? (
              <div className="flex items-center gap-2">
                {run.status === 'running' || run.status === 'pending' ? (
                  <button type="button" onClick={() => void binding.cancel()} disabled={Boolean(binding.pending)} className="cx-btn cx-btn--danger px-3 py-1.5 text-xs">取消</button>
                ) : null}
                {['failed', 'cancelled', 'interrupted'].includes(run.status) ? (
                  <button type="button" onClick={() => void binding.resume()} disabled={Boolean(binding.pending)} className="cx-btn cx-btn--ghost px-3 py-1.5 text-xs">继续同一运行</button>
                ) : null}
                <button type="button" onClick={() => void binding.refresh()} disabled={binding.loading || !binding.sessionId} aria-label="刷新工程快照" className="cx-icon-btn"><IconRefresh className="h-4 w-4" /></button>
              </div>
            ) : null}
          </div>
          <div className="p-4">
            <GoalComposer binding={composerBinding} role={role} onRoleChange={onRoleChange} />
            {binding.error ? <div role="alert" className="mt-3 rounded-lg border border-[#eccbc6] bg-[var(--cx-red-soft)] px-3 py-2 text-sm text-[var(--cx-red)]">{binding.error}</div> : null}
            {run ? (
              <div className="mt-4 space-y-1.5 border-t border-dashed border-[var(--cx-line)] pt-3 font-mono text-[0.7rem] text-[var(--cx-ink-dim)]">
                <p className="break-all">run · {run.run_id}</p>
                <p>workflow · {run.workflow} / entry · {run.role} / 实际检查 · {pendingSnapshot ? '等待快照' : `${run.checks.length} 项`}</p>
                {(run.event_delivery_error || run.native_observation_error) ? <p role="alert" className="text-[var(--cx-amber)]">实时事件或原生上下文观测不可用，详见快照。</p> : null}
              </div>
            ) : null}
          </div>
        </section>
        {run?.text ? (
          <section className="cx-card cx-enter cx-enter-6">
            <div className="cx-card-head"><h2 className="cx-card-title">角色回复 · TRANSCRIPT</h2></div>
            <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap p-4 text-sm leading-relaxed text-[var(--cx-ink)] cx-scroll">{run.text}</pre>
          </section>
        ) : null}
      </div>
      <StatusDock connected={binding.connected} receiptCount={run?.checks.length ?? 0} />
      {selectedContext ? <ContextViewerModal initialTab="final-request" contextSnapshotRef={selectedContext.ref} roleId={selectedContext.role} workspace={workspace} onClose={() => setSelectedContext(null)} /> : null}
    </div>
  );
}
