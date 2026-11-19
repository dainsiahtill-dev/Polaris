import { useState, type ReactElement } from 'react';
import type { EngineeringRole } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { ROLE_NAV_LABELS, ROLE_ORDER } from './format';
import { IconClock } from './icons';

const SELECT_CLASS = 'cx-input cx-mono text-xs';

/** Goal entry: one sentence starts an unattended evidence-chained run. */
export function GoalComposer({ binding, role, onRoleChange }: {
  binding: EngineeringRunBinding;
  role: EngineeringRole;
  onRoleChange: (role: EngineeringRole) => void;
}): ReactElement {
  const [instruction, setInstruction] = useState('');
  const [checks, setChecks] = useState('');
  const [workflow, setWorkflow] = useState<'direct' | 'collaborative'>('direct');
  const busy = Boolean(binding.pending);
  const canStart = !binding.projection.snapshot || (binding.projection.snapshot.status !== 'running' && binding.projection.snapshot.status !== 'pending');
  const start = async () => {
    const ok = await binding.start({
      instruction: instruction.trim(),
      role,
      workflow: role === 'director' ? workflow : 'direct',
      required_checks: checks.split('\n').map((line) => line.trim()).filter(Boolean),
    });
    if (ok) setInstruction('');
  };
  const discussionObservation = binding.discussionMetadata?.native_request_observation;
  const discussionRef = binding.discussionMetadata?.context_snapshot_ref;
  const readableDiscussionRef = discussionObservation && typeof discussionObservation === 'object' && (discussionObservation as Record<string, unknown>).snapshot_status === 'available' && typeof discussionRef === 'string' && /^[a-f0-9]{24}$/.test(discussionRef) ? discussionRef : null;
  return (
    <div className="space-y-3.5">
      <div>
        <label htmlFor="engineering-instruction" className="cx-eyebrow">工程目标 · GOAL</label>
        <textarea
          id="engineering-instruction"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          rows={4}
          placeholder="描述目标、约束和预期结果。例如：做一个 Three.js 网页，一只鹈鹕在公园骑单车，60fps，附带测试。"
          className="cx-input mt-1.5"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <span className="cx-eyebrow">入口角色 · ENTRY</span>
          <div className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label="入口角色">
            {ROLE_ORDER.map((entry) => (
              <button
                key={entry}
                type="button"
                aria-pressed={role === entry}
                onClick={() => onRoleChange(entry)}
                className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${role === entry ? 'border-[var(--cx-green)] bg-[var(--cx-green-soft)] text-[var(--cx-green)]' : 'border-[var(--cx-line)] bg-white text-[var(--cx-ink-dim)] hover:border-[var(--cx-line-strong)]'}`}
              >
                {ROLE_NAV_LABELS[entry]}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label htmlFor="engineering-workflow" className="cx-eyebrow">协作方式 · WORKFLOW</label>
          <select id="engineering-workflow" value={workflow} onChange={(event) => setWorkflow(event.target.value as 'direct' | 'collaborative')} disabled={role !== 'director'} className={`${SELECT_CLASS} mt-1.5`}>
            <option value="direct">Director 直接处理（默认）</option>
            {role === 'director' ? <option value="collaborative">PM / CE / Director 协作</option> : null}
          </select>
        </div>
      </div>
      <details className="group">
        <summary className="cursor-pointer list-none text-xs text-[var(--cx-ink-dim)] hover:text-[var(--cx-ink)]">
          <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>验证命令（可选，每行一条；留空由 Director 自行选择并执行）
        </summary>
        <textarea
          value={checks}
          onChange={(event) => setChecks(event.target.value)}
          rows={2}
          placeholder={'npm test\nnpm run build'}
          className="cx-input cx-mono mt-2 text-xs"
          aria-label="验证命令"
        />
      </details>
      <div className="flex flex-wrap items-center gap-2.5">
        <button type="button" onClick={() => void start()} disabled={busy || !canStart || !instruction.trim()} className="cx-btn cx-btn--primary">
          <IconClock className="h-4 w-4" />
          {binding.pending === 'start' ? '启动中…' : '开始执行'}
        </button>
        <button type="button" onClick={() => void binding.discuss(role, instruction)} disabled={busy || !instruction.trim()} className="cx-btn cx-btn--ghost">讨论方案</button>
        <span className="text-[0.68rem] text-[var(--cx-ink-faint)]">讨论只返回建议，不创建运行；权限由服务器配置。</span>
      </div>
      {binding.discussion ? (
        <div className="rounded-xl border border-[var(--cx-line)] bg-[#fbfaf5] p-3.5">
          <p className="cx-eyebrow">角色建议 · ADVISORY ONLY</p>
          <pre className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-[var(--cx-ink)]">{binding.discussion}</pre>
          {readableDiscussionRef ? <p className="mt-2 font-mono text-[0.7rem] text-[var(--cx-cyan)]">快照 {readableDiscussionRef.slice(0, 12)}… 可在 ContextOS 中审阅最终请求</p> : <p className="mt-2 text-xs text-[var(--cx-ink-faint)]">讨论快照不可用</p>}
        </div>
      ) : null}
    </div>
  );
}
