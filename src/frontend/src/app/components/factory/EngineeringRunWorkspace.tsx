import { useState, type ReactNode } from 'react';
import { ArrowLeft, Loader2, Play, RefreshCw, Square } from 'lucide-react';
import type { EngineeringRole, EngineeringEvent } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { ENGINEERING_ROLES } from '@/runtime/engineeringRunProjection';
import { ContextViewerModal } from '@/app/components/contextos/ContextViewerModal';
const names = { pm: 'PM', chief_engineer: 'Chief Engineer', director: 'Director', qa: 'QA' };
const statuses: Record<string, string> = { not_called: '未调用', running: '进行中', completed: '执行结束', failed: '执行失败', cancelled: '已取消', interrupted: '已中断', pending: '等待执行', unknown: '未知' };
const verification: Record<string, string> = { passed: '已通过', failed: '未通过', not_run: '未运行验证', unknown: '未知' };
function text(value: unknown): string { return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2); }
function contextRef(event: EngineeringEvent): string | null {
  const payload = event.payload;
  const raw = payload.context_snapshot_ref;
  return payload.snapshot_status === 'available' && typeof raw === 'string' && /^[a-f0-9]{24}$/.test(raw) ? raw : null;
}
export function EngineeringRunWorkspace({ workspace, binding, role = 'director', onBackToMain, onLegacy, onOpenContext }: {
  workspace: string;
  binding: EngineeringRunBinding;
  role?: EngineeringRole;
  onBackToMain: () => void;
  onLegacy?: () => void; onOpenContext?: () => void;
}) {
  const [instruction, setInstruction] = useState('');
  const [checks, setChecks] = useState('');
  const [workflow, setWorkflow] = useState<'direct' | 'collaborative'>('direct');
  const [selectedContext, setSelectedContext] = useState<{
    ref: string;
    role: string;
  } | null>(null);
  const discussionObservation = binding.discussionMetadata?.native_request_observation;
  const discussionRef = binding.discussionMetadata?.context_snapshot_ref;
  const readableDiscussionRef = discussionObservation && typeof discussionObservation === 'object' && (discussionObservation as Record<string, unknown>).snapshot_status === 'available' && typeof discussionRef === 'string' && /^[a-f0-9]{24}$/.test(discussionRef) ? discussionRef : null;
  const run = binding.projection.snapshot;
  const verificationCommands = run?.verification_commands ?? run?.required_checks ?? [];
  const snapshotUnavailable = Boolean(binding.projection.error || binding.projection.snapshotRequiredSeq);
  const qaVerdict = snapshotUnavailable ? '未知（快照不可用）' : run?.roles.qa === 'not_called' || run?.qa_verdict === 'not_run' ? '未调用' : ({ approved: '已批准', rejected: '已拒绝', unknown: '未知' }[run?.qa_verdict || 'unknown']);
  const projectOutcome = snapshotUnavailable ? '未知（快照不可用）' : ({ not_accepted: '未验收', accepted: '已验收', rejected: '已拒绝', unknown: '未知（未提供验收结论）' }[run?.project_outcome || 'unknown']);
  const acceptanceBasis = run?.acceptance_basis === 'checks_only' ? '仅检查' : run?.acceptance_basis === 'independent_qa' ? '独立QA' : '依据未知';
  const busy = Boolean(binding.pending);
  const canStart = !run || run.status !== 'running' && run.status !== 'pending';
  const canCancel = run?.status === 'running' || run?.status === 'pending';
  const canResume = run && ['failed', 'cancelled', 'interrupted'].includes(run.status);
  const events = binding.projection.events.filter((event) => !['message', 'run_updated'].includes(event.kind)).slice(-100).reverse();
  return <div className="polaris-soft-scope soft-app-bg flex h-full min-h-0 flex-col overflow-hidden text-slate-100" data-testid="engineering-run-workspace">
    <header className="soft-panel-subtle flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        <button type="button" onClick={onBackToMain} aria-label="返回主界面" className="rounded-lg p-3 text-slate-300 hover:bg-white/10"><ArrowLeft className="h-4 w-4" /></button>
        <div className="min-w-0"><h1 className="text-sm font-semibold">Engineering v2 · {names[role]}</h1><p className="truncate text-xs text-slate-400" title={workspace}>{workspace}</p></div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {onOpenContext && <button type="button" onClick={onOpenContext} className="rounded-lg border border-white/10 px-3 py-3 text-xs text-cyan-100">ContextOS</button>}
        {onLegacy && <button type="button" onClick={onLegacy} className="rounded-lg border border-white/10 px-3 py-3 text-xs text-slate-300">查看历史界面</button>}
        <button type="button" onClick={() => void binding.refresh()} disabled={binding.loading || !binding.sessionId} aria-label="刷新工程快照" className="rounded-lg border border-white/10 p-3 disabled:opacity-40"><RefreshCw className="h-4 w-4" /></button>
        {canCancel && <button type="button" onClick={() => void binding.cancel()} disabled={busy} className="flex items-center gap-2 rounded-lg bg-red-500/15 px-3 py-3 text-sm text-red-100 disabled:opacity-40"><Square className="h-4 w-4" />取消</button>}
        {canResume && <button type="button" onClick={() => void binding.resume()} disabled={busy} className="rounded-lg border border-emerald-400/25 px-3 py-3 text-sm text-emerald-100 disabled:opacity-40">继续同一运行</button>}
      </div>
    </header>
    <main className="grid min-h-0 flex-1 gap-4 overflow-auto p-4 xl:grid-cols-[minmax(280px,380px)_minmax(0,1fr)]">
      <section className="space-y-4">
        <div className="soft-panel-subtle space-y-3 rounded-xl p-4">
          <label htmlFor="engineering-instruction" className="block text-sm font-medium">工程目标</label>
          <textarea id="engineering-instruction" value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={6} placeholder="描述目标、约束和预期结果" className="w-full rounded-lg border border-white/15 bg-slate-950/50 p-3 text-sm outline-none focus:border-emerald-400" />
          <label className="block text-sm" htmlFor="engineering-workflow">协作方式</label>
          <select id="engineering-workflow" value={workflow} onChange={(event) => setWorkflow(event.target.value as 'direct' | 'collaborative')} disabled={role !== 'director'} className="w-full rounded-lg border border-white/15 bg-slate-950 p-3 text-sm"><option value="direct">当前角色直接处理</option>{role === 'director' && <option value="collaborative">PM、Chief Engineer、Director 协作</option>}</select>
          <label htmlFor="engineering-checks" className="block text-sm">验证命令</label>
          <textarea id="engineering-checks" value={checks} onChange={(event) => setChecks(event.target.value)} rows={3} placeholder="可选；每行一条。留空由 Agent 选择并执行验证命令" className="w-full rounded-lg border border-white/15 bg-slate-950/50 p-3 font-mono text-xs outline-none focus:border-emerald-400" />
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void binding.discuss(role, instruction)} disabled={busy || !instruction.trim()} className="rounded-lg border border-white/15 px-3 py-3 text-sm disabled:opacity-40">讨论方案</button>
            <button type="button" disabled={busy || !canStart || !instruction.trim()} onClick={() => void binding.start({ instruction: instruction.trim(), role, workflow: role === 'director' ? workflow : 'direct', required_checks: checks.split('\n').map((line) => line.trim()).filter(Boolean) })} className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-3 text-sm font-medium hover:bg-emerald-500 disabled:opacity-40">{binding.pending === 'start' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}{binding.pending === 'start' ? '启动中…' : '开始执行'}</button>
          </div>
          <p className="text-xs leading-relaxed text-slate-400">讨论只返回建议。执行与验证结果分别显示；权限由服务器配置。</p>
          {binding.discussion && <div><pre className="whitespace-pre-wrap rounded-lg bg-black/20 p-3 text-sm">{binding.discussion}</pre>{readableDiscussionRef ? <button type="button" onClick={() => setSelectedContext({ ref: readableDiscussionRef, role })} className="mt-2 rounded-lg border border-cyan-400/25 px-3 py-3 text-xs text-cyan-100">查看讨论最终请求</button> : <p className="mt-2 text-xs text-slate-400">讨论快照不可用</p>}</div>}
        </div>
        {binding.error && <div role="alert" className="rounded-lg border border-red-400/30 bg-red-500/10 p-3 text-sm text-red-100">{binding.error}</div>}
        {run && <div className="soft-panel-subtle space-y-2 rounded-xl p-4 text-sm">
          <p className="break-all font-mono text-xs text-slate-400">{run.run_id}</p>
          <p className="text-xs text-slate-400">运行入口角色：{names[run.role]}</p>
          <p role="status" aria-atomic="true">运行状态：{binding.projection.snapshotRequiredSeq ? '等待完整快照' : statuses[run.status] || '未知'}</p>
          <p data-testid="engineering-verification">验证结果：{binding.projection.snapshotRequiredSeq ? '未知（等待完整快照）' : binding.projection.error ? '未知（快照不可用）' : verification[run.verification] || '未知'}</p>
          <p data-testid="engineering-qa-verdict">QA 结论：{qaVerdict}{run.require_qa === true ? '（必需）' : ''}</p>
          <p data-testid="engineering-project-outcome">项目验收：{projectOutcome}{!snapshotUnavailable && run.project_outcome === 'accepted' ? `（${acceptanceBasis}）` : ''}</p>
          <p className="text-xs text-slate-400">实际检查：{binding.projection.snapshotRequiredSeq ? '等待完整快照' : `${run.checks.length} 项`}</p>
          {run.truncated_fields?.length ? <p className="text-xs text-amber-200">当前为截断预览，可刷新快照读取完整记录。</p> : null}
          {run.checks.map((check, index) => <div key={check.call_id} className={`rounded-lg border p-3 ${check.ok && check.exit_code === 0 ? 'border-emerald-500/20' : 'border-red-500/30'}`}><p className="break-all font-mono text-xs text-slate-300">{verificationCommands[index] ?? '验证命令未知'}</p><p className="text-xs text-slate-400">exit {check.exit_code ?? '未知'}{check.interrupted ? ' · 已中断' : ''}</p><pre className="whitespace-pre-wrap text-xs">{check.content}</pre></div>)}
          {Boolean(run.event_delivery_error) && <p role="alert" className="text-amber-200">实时事件不可用：{text(run.event_delivery_error)}</p>}
          {Boolean(run.native_observation_error) && <p role="alert" className="text-amber-200">原生上下文观测不可用：{text(run.native_observation_error)}</p>}
        </div>}
      </section>
      <section className="min-w-0 space-y-4">
        <div className="soft-panel-subtle rounded-xl p-4">
          <p className="mb-3 text-xs text-slate-400">{binding.connected ? '实时连接已建立' : binding.reconnecting ? '正在重新连接；当前显示最后快照' : '实时连接已断开；当前显示最后快照'}{binding.connectionError ? ` · ${binding.connectionError}` : ''}</p>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">{ENGINEERING_ROLES.map((key) => <div key={key} data-testid={`engineering-role-${key}`} className="rounded-lg border border-white/10 p-3"><p className="text-xs text-slate-400">{names[key]}</p><p className="mt-1 text-sm">{run ? statuses[run.roles[key]] || '未知' : '未调用'}</p></div>)}</div>
        </div>
        {run?.text && <div className="soft-panel-subtle rounded-xl p-4"><h2 className="mb-2 text-sm font-medium">角色回复</h2><pre className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">{run.text}</pre></div>}
        <div className="soft-panel-subtle rounded-xl p-4"><h2 className="text-sm font-medium">工具与运行记录</h2>{binding.projection.historyWindowed && <p className="mt-2 text-xs text-slate-400">仅展示最近记录窗口，更早记录未加载。</p>}
          {!events.length && <p className="mt-3 text-sm text-slate-400">{binding.loading ? '正在读取快照…' : '尚无可展示的运行记录'}</p>}
          <div className="mt-3 space-y-2">{events.map((event) => {
            const ref = contextRef(event);
            const failed = event.payload.ok === false || event.kind.endsWith('failed');
            return <article key={event.seq} className={`rounded-lg border p-3 ${failed ? 'border-red-400/30 bg-red-500/5' : 'border-white/10'}`}>
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium">{event.role} · {event.kind === 'tool_started' ? '工具调用中' : event.kind === 'tool_observed' ? '工具结果' : event.kind}</span><span className="text-xs text-slate-500">#{event.seq}</span></div>
              {ref && <button type="button" className="mt-2 rounded border border-cyan-400/25 px-3 py-2 text-xs text-cyan-100" onClick={() => setSelectedContext({ ref, role: event.role })}>查看最终请求</button>}
              {Array.isArray(event.payload.truncated_fields) && event.payload.truncated_fields.length > 0 && <p className="mt-2 text-xs text-amber-200">输出已截断；完整记录保留在运行快照中。</p>}
              {typeof event.payload.tool === 'string' && <p className="mt-2 font-mono text-xs text-slate-300">{event.payload.tool}</p>}
              {typeof event.payload.content === 'string' ? <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-300">{event.payload.content}</pre> : <pre className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-400">{text(event.payload)}</pre>}
            </article>;
          })}</div>
        </div>
      </section>
    </main>
    {selectedContext && <ContextViewerModal initialTab="final-request" contextSnapshotRef={selectedContext.ref} roleId={selectedContext.role} workspace={workspace} onClose={() => setSelectedContext(null)} />}
  </div>;
}
/** Hook-free engine dispatch keeps old component effects behind their original mode. */
export function EngineeringModeBoundary({ enabled, binding, onModeChange, workspace, role, onBackToMain, onOpenContext, children }: {
  enabled?: boolean;
  binding?: EngineeringRunBinding;
  onModeChange?: (enabled: boolean) => void;
  workspace: string;
  role: EngineeringRole;
  onBackToMain: () => void;
  onOpenContext?: () => void;
  children: ReactNode;
}) {
  if (enabled && binding)
    return <EngineeringRunWorkspace workspace={workspace} binding={binding} role={role} onBackToMain={onBackToMain} onOpenContext={onOpenContext} onLegacy={() => onModeChange?.(false)} />;
  if (!onModeChange)
    return <>{children}</>;
  return <div className="flex h-full min-h-0 flex-col"><div className="flex shrink-0 items-center justify-end border-b border-white/10 bg-slate-950 px-4 py-1"><button type="button" className="rounded px-3 py-3 text-xs text-emerald-200 hover:bg-white/5" onClick={() => onModeChange(true)}>使用 Engineering v2</button></div><div className="min-h-0 flex-1">{children}</div></div>;
}
