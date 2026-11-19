import { useEffect, useMemo, useState, type ReactElement } from 'react';
import type { EngineeringEvent, EngineeringRole } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { ContextViewerModal } from '@/app/components/contextos/ContextViewerModal';
import { GoalComposer } from './GoalComposer';
import { RoleTracks } from './RoleTracks';
import { ToolTimeline } from './ToolTimeline';
import { VerdictBoard } from './VerdictBoard';
import {
  OUTCOME_LABELS, QA_LABELS, RUN_STATUS_LABELS, VERIFICATION_LABELS, eventTone,
  formatClock, outcomeTone, qaTone, receiptId, runShortId, runStatusTone, verificationTone,
} from './format';
import {
  IconClapper, IconCompass, IconFlag, IconInfo, IconInbox, IconLayers, IconShield, IconTerminal, IconUser,
} from './icons';

function timelineEvents(events: EngineeringEvent[]): EngineeringEvent[] {
  return events;
}

function firstLine(text: string | undefined): string {
  return (text || '').split('\n')[0].trim();
}

/** ③⑦ Run live view: roles, journal, selected receipt, verdict strip. */
export function MissionControlView({ workspace, binding, role }: {
  workspace: string;
  binding: EngineeringRunBinding;
  role: EngineeringRole;
}): ReactElement {
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [selectedContext, setSelectedContext] = useState<{ ref: string; role: string } | null>(null);
  const [goal, setGoal] = useState<string | null>(null);
  const openContext = (ref: string, eventRole: string) => setSelectedContext({ ref, role: eventRole });
  const projection = binding.projection;
  const run = projection.snapshot;
  const events = useMemo(() => timelineEvents(projection.events), [projection.events]);
  const selectedEvent = events.find((event) => event.seq === selectedSeq) ?? null;
  const failedEvent = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (event && eventTone(event) === 'bad') return event;
    }
    return null;
  }, [events]);
  useEffect(() => {
    if (selectedSeq !== null && !events.some((event) => event.seq === selectedSeq)) setSelectedSeq(null);
  }, [events, selectedSeq]);
  if (!run) {
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 cx-scroll" data-testid="engineering-run-workspace">
        <div>
          <p className="cx-eyebrow">Engineering v2 · {role === 'pm' ? 'PM' : role === 'chief_engineer' ? 'Chief Engineer' : role === 'qa' ? 'QA' : 'Director'}</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">新建工程运行</h1>
          <p className="mt-1 text-sm text-[var(--cx-ink-dim)]">为当前工作区创建一次工程运行，执行实现、验证与验收。</p>
        </div>
        <GoalComposer binding={binding} workspace={workspace} role={role} onStarted={(instruction) => setGoal(instruction)} />
        <p className="cx-faint text-xs">示例工作区 · 工作区 {workspace || '未绑定'}</p>
      </div>
    );
  }
  const busy = Boolean(binding.pending);
  const connTone = binding.connected ? 'ok' : binding.reconnecting ? 'warn' : 'bad';
  const failedCheck = run.checks.find((check) => !(check.ok && check.exit_code === 0));
  const banner = run.qa_verdict === 'rejected'
    ? { tone: 'bad' as const, text: '独立 QA 已退回本轮候选，Director 将在同一运行中继续修复。' }
    : (failedCheck || failedEvent) && run.status === 'running'
      ? { tone: 'warn' as const, text: '验证未通过，Director 正在根据验证输出修复。' }
      : run.status === 'running'
        ? { tone: 'ok' as const, text: '运行进行中，证据链随工具调用实时更新。' }
        : run.project_outcome === 'accepted'
          ? { tone: 'ok' as const, text: `项目已验收（${run.acceptance_basis === 'independent_qa' ? '独立QA' : '检查'}）。完整运行记录保持可回看。` }
          : run.status === 'failed'
            ? { tone: 'bad' as const, text: '运行失败。沿下方事件记录可定位终止位置。' }
            : { tone: 'warn' as const, text: `运行${RUN_STATUS_LABELS[run.status]}，可继续同一运行或新建运行。` };
  const title = firstLine(goal ?? undefined) || firstLine(run.text) || '工程运行';
  const workflowLabel = run.workflow === 'collaborative' ? 'collaborative' : 'direct';
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4 cx-scroll" data-testid="engineering-run-workspace">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="cx-eyebrow">Engineering v2 · {role === 'pm' ? 'PM' : role === 'chief_engineer' ? 'Chief Engineer' : role === 'qa' ? 'QA' : 'Director'}</p>
          <h1 className="mt-1 text-2xl font-bold tracking-tight">{run.project_outcome === 'accepted' ? '交付验收' : '工程运行实况'}</h1>
          <p className="mt-1.5 flex flex-wrap items-center gap-2.5 text-sm text-[var(--cx-ink-dim)]">
            <span className="font-semibold text-[var(--cx-ink)]">{title}</span>
            <span aria-hidden="true" className="text-[var(--cx-line-strong)]">|</span>
            <span className="cx-mono text-xs">{workflowLabel}</span>
            <span aria-hidden="true" className="text-[var(--cx-line-strong)]">|</span>
            <span className={`cx-pill cx-pill--${runStatusTone(run.status)}`}>
              {run.status === 'running' ? <span className="cx-dot cx-dot--run cx-dot--pulse" /> : null}
              {RUN_STATUS_LABELS[run.status]}
            </span>
            <span className="cx-mono text-xs text-[var(--cx-ink-faint)]">{runShortId(run.session_id)}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          {run.status === 'running' || run.status === 'pending' ? (
            <button type="button" onClick={() => void binding.cancel()} disabled={busy} className="cx-btn cx-btn--ghost">取消运行</button>
          ) : null}
          {['failed', 'cancelled', 'interrupted'].includes(run.status) ? (
            <button type="button" onClick={() => void binding.resume()} disabled={busy} className="cx-btn cx-btn--primary">继续同一运行</button>
          ) : null}
          <button type="button" onClick={() => void binding.refresh()} disabled={binding.loading} aria-label="刷新工程快照" className="cx-icon-btn" title="刷新工程快照">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4"><path d="M20 11A8 8 0 0 0 5.6 6.6L4 8.5M4 13a8 8 0 0 0 14.4 4.4L20 15.5" /><path d="M4 4v4.5h4.5M20 20v-4.5h-4.5" /></svg>
          </button>
        </div>
      </div>
      <RoleTracks roles={run.roles} />
      <div className={`cx-banner cx-banner--${banner.tone} cx-enter cx-enter-2`} role="status">
        <IconInfo className="h-4.5 w-4.5 shrink-0" />
        <p>{banner.text}</p>
      </div>
      <div className={`cx-conn cx-conn--${connTone} flex items-center gap-2 px-3.5 py-1.5 text-xs`} role="status">
        <span className={`cx-dot ${binding.connected ? 'cx-dot--ok' : binding.reconnecting ? 'cx-dot--warn' : 'cx-dot--bad'}`} />
        <p className="text-[var(--cx-ink-dim)]">
          {binding.connected ? '实时连接已建立' : binding.reconnecting ? '正在重新连接；显示最后快照，恢复后自动补齐事件' : '实时连接已断开；显示最后快照，等待重连'}
          {binding.projection.historyWindowed ? ' · 仅展示最近记录窗口' : ''}
        </p>
      </div>
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-12">
        <section className="cx-card flex min-h-[20rem] flex-col overflow-hidden xl:col-span-7">
          <div className="cx-card-head">
            <h2 className="cx-card-title"><IconInbox className="h-4.5 w-4.5 text-[var(--cx-teal)]" />运行实况</h2>
            <span className="cx-pill cx-pill--idle">{events.length} 条记录</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto cx-scroll">
            <ToolTimeline
              events={events}
              selectedSeq={selectedEvent?.seq ?? null}
              onSelect={(event) => setSelectedSeq(event.seq)}
              onOpenContext={openContext}
              emptyHint={binding.loading ? '正在读取快照…' : '尚无可展示的记录'}
            />
          </div>
        </section>
        <section className="cx-card flex min-h-[20rem] flex-col overflow-hidden xl:col-span-5">
          <div className="cx-card-head">
            <h2 className="cx-card-title"><IconTerminal className="h-4.5 w-4.5 text-[var(--cx-teal)]" />输出详情</h2>
            {selectedEvent ? (
              <span className="flex items-center gap-2">
                <span className="cx-mono text-xs text-[var(--cx-ink-dim)]">{receiptId(selectedEvent.seq)}</span>
                {eventTone(selectedEvent) === 'bad' ? <span className="cx-pill cx-pill--bad">检查未通过</span> : eventTone(selectedEvent) === 'ok' ? <span className="cx-pill cx-pill--ok">已记录</span> : <span className="cx-pill cx-pill--info">事件</span>}
              </span>
            ) : <span className="cx-pill cx-pill--idle">未选择</span>}
          </div>
          {selectedEvent ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-[var(--cx-line)] px-4 py-2 font-mono text-xs text-[var(--cx-ink-dim)]">
                工具 · {typeof selectedEvent.payload.tool === 'string' ? selectedEvent.payload.tool : selectedEvent.kind}
                <span className="ml-2 text-[var(--cx-ink-faint)]">{formatClock(selectedEvent.created_at)} · #{selectedEvent.seq}</span>
              </div>
              <div className="flex min-h-0 flex-1 flex-col p-4">
                <pre className="cx-terminal min-h-0 flex-1 overflow-y-auto cx-scroll">{typeof selectedEvent.payload.content === 'string' && selectedEvent.payload.content ? selectedEvent.payload.content : JSON.stringify(selectedEvent.payload, null, 2)}</pre>
                {eventTone(selectedEvent) === 'bad' ? <p className="mt-2 flex items-center gap-1.5 text-xs text-[var(--cx-red)]"><IconInfo className="h-3.5 w-3.5" />检查未通过，已回到修复流程</p> : null}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
              <IconFlag className="h-6 w-6 text-[var(--cx-ink-faint)]" />
              <p className="text-sm text-[var(--cx-ink-dim)]">点选左侧任意一条记录查看原始输出；失败的验证会显示预期值与实际值。</p>
            </div>
          )}
        </section>
      </div>
      <section className="cx-card cx-enter cx-enter-5">
        <VerdictBoard
          status={run.status}
          verification={run.verification}
          qaVerdict={run.qa_verdict}
          requireQa={run.require_qa}
          projectOutcome={run.project_outcome}
          acceptanceBasis={run.acceptance_basis}
          pendingSnapshot={Boolean(projection.error || projection.snapshotRequiredSeq)}
        />
      </section>
      {run.text ? (
        <section className="cx-card">
          <div className="cx-card-head"><h2 className="cx-card-title"><IconClapper className="h-4.5 w-4.5 text-[var(--cx-teal)]" />最终回复</h2></div>
          <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap p-4 text-sm leading-relaxed cx-scroll">{run.text}</pre>
        </section>
      ) : null}
      <footer className="flex flex-wrap items-center gap-x-6 gap-y-1.5 border-t border-[var(--cx-line)] pb-1 pt-3 font-mono text-[0.68rem] text-[var(--cx-ink-dim)]">
        <span className="flex items-center gap-1.5"><IconLayers className="h-3.5 w-3.5" />示例运行 · {runShortId(run.session_id)}</span>
        <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
        <span className="flex items-center gap-1.5"><IconCompass className="h-3.5 w-3.5" />ENGINEERING V2 · 无人值守工程</span>
        <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
        <span className="flex items-center gap-1.5"><IconUser className="h-3.5 w-3.5" />入口 {role}</span>
        <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
        <span className="flex items-center gap-1.5"><IconShield className="h-3.5 w-3.5" />验证 {VERIFICATION_LABELS[run.verification]} · QA {QA_LABELS[run.qa_verdict ?? 'unknown']} · 项目 {OUTCOME_LABELS[run.project_outcome ?? 'unknown']}</span>
      </footer>
      {selectedContext ? <ContextViewerModal initialTab="final-request" contextSnapshotRef={selectedContext.ref} roleId={selectedContext.role} workspace={workspace} onClose={() => setSelectedContext(null)} /> : null}
    </div>
  );
}
