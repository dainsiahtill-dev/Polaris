import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringRole } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { ROLE_LABELS } from './format';
import { IconFolder, IconLayers, IconShield, IconUser } from './icons';

function workspaceBase(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() || path;
}

function EnvRow({ icon, label, value }: { icon: ReactElement; label: string; value: string }): ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-[var(--cx-line)] py-2.5 last:border-0">
      <span className="flex items-center gap-2.5 text-sm text-[var(--cx-ink-dim)]">{icon}{label}</span>
      <span className="cx-mono truncate text-xs text-[var(--cx-ink)]">{value}</span>
    </div>
  );
}

/** ② New-run page: one goal sentence starts an unattended, evidence-chained run. */
export function GoalComposer({ binding, workspace, role }: {
  binding: EngineeringRunBinding;
  workspace: string;
  role: EngineeringRole;
}): ReactElement {
  const [instruction, setInstruction] = useState('');
  const [checks, setChecks] = useState('');
  const [workflow, setWorkflow] = useState<'direct' | 'collaborative'>('direct');
  const busy = Boolean(binding.pending);
  const snapshot = binding.projection.snapshot;
  const canStart = !snapshot || (snapshot.status !== 'running' && snapshot.status !== 'pending');
  const checkLines = useMemo(() => checks.split('\n').length, [checks]);
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
  const workflowCards: Array<{ value: 'direct' | 'collaborative'; title: string; en: string; desc: string }> = [
    { value: 'direct', title: '直接执行', en: 'direct', desc: `${ROLE_LABELS[role]} 直接读写、运行和验证` },
    { value: 'collaborative', title: '协作执行', en: 'collaborative', desc: 'PM 规划 · CE 设计 · Director 执行' },
  ];
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
      <div className="cx-card cx-enter cx-enter-2 xl:col-span-8">
        <div className="space-y-5 p-5">
          <div>
            <label htmlFor="engineering-instruction" className="text-base font-bold">工程目标</label>
            <textarea
              id="engineering-instruction"
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              rows={5}
              placeholder="描述目标、约束和预期结果。例如：做一个可离线打开的 Three.js 网页，鹈鹕在公园骑单车，支持暂停和调速，目标 60 FPS，并附自动化测试。"
              className="cx-input mt-2 text-[0.95rem] leading-relaxed"
            />
          </div>
          <fieldset>
            <legend className="sr-only">协作方式</legend>
            <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="协作方式">
              {workflowCards.map((card) => {
                const disabled = card.value === 'collaborative' && role !== 'director';
                const selected = workflow === card.value;
                return (
                  <button
                    key={card.value}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    disabled={disabled}
                    onClick={() => setWorkflow(card.value)}
                    className={`flex items-start gap-3 rounded-xl border p-4 text-left transition-all disabled:opacity-45 ${selected ? 'border-[var(--cx-teal)] bg-[var(--cx-teal-mist)] shadow-[0_0_0_3px_rgba(52,165,131,0.10)]' : 'border-[var(--cx-line-strong)] bg-white hover:border-[#aeb6c4]'}`}
                  >
                    <span aria-hidden="true" className={`mt-0.5 flex h-4.5 w-4.5 flex-none items-center justify-center rounded-full border-2 ${selected ? 'border-[var(--cx-teal)]' : 'border-[#b8bfca]'}`}>
                      {selected ? <span className="h-2 w-2 rounded-full bg-[var(--cx-teal)]" /> : null}
                    </span>
                    <span>
                      <span className="block text-sm font-bold">{card.title} <span className="cx-mono ml-1 text-xs font-medium text-[var(--cx-ink-faint)]">{card.en}</span></span>
                      <span className="mt-1 block text-xs leading-relaxed text-[var(--cx-ink-dim)]">{card.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </fieldset>
          <div>
            <span className="text-base font-bold">验证命令 <span className="ml-1 text-xs font-normal text-[var(--cx-ink-faint)]">（可选）</span></span>
            <div className="cx-code cx-code-input mt-2">
              <span className="cx-code-gutter" aria-hidden="true">
                {Array.from({ length: Math.max(2, checkLines) }, (_, index) => <span key={index} className="block">{index + 1}</span>)}
              </span>
              <textarea
                value={checks}
                onChange={(event) => setChecks(event.target.value)}
                rows={Math.max(2, checkLines)}
                placeholder="npm run build&#10;npm test"
                aria-label="验证命令"
                className="cx-code-area"
                spellCheck={false}
              />
            </div>
            <p className="mt-1.5 text-xs text-[var(--cx-ink-faint)]">留空由 Director 自行选择并执行验证命令。</p>
          </div>
          <div className="flex flex-wrap items-center gap-3 border-t border-[var(--cx-line)] pt-4">
            <button type="button" onClick={() => void start()} disabled={busy || !canStart || !instruction.trim()} className="cx-btn cx-btn--primary">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4"><path d="M7 4.5 19 12 7 19.5Z" /></svg>
              {binding.pending === 'start' ? '启动中…' : '开始执行'}
            </button>
            <button type="button" onClick={() => void binding.discuss(role, instruction)} disabled={busy || !instruction.trim()} className="cx-btn cx-btn--ghost">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4"><path d="M4 5h16v11H9l-5 4Z" /></svg>
              讨论方案
            </button>
            <span className="text-xs text-[var(--cx-ink-faint)]">只读建议，不创建运行</span>
          </div>
          {binding.discussion ? (
            <div className="rounded-xl border border-[var(--cx-line)] bg-[#fbfbfc] p-4">
              <p className="cx-eyebrow">角色建议 · ADVISORY ONLY</p>
              <pre className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-[var(--cx-ink)] cx-scroll">{binding.discussion}</pre>
              {readableDiscussionRef ? <p className="mt-2 font-mono text-[0.7rem] text-[#1f7a8c]">快照 {readableDiscussionRef.slice(0, 12)}… 可在 ContextOS 中审阅最终请求</p> : <p className="mt-2 text-xs text-[var(--cx-ink-faint)]">讨论快照不可用</p>}
            </div>
          ) : null}
        </div>
      </div>
      <div className="space-y-4 xl:col-span-4">
        <div className="cx-card cx-enter cx-enter-3 p-4">
          <h2 className="text-base font-bold">运行环境</h2>
          <div className="mt-2">
            <EnvRow icon={<IconLayers className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />} label="实例" value={workspaceBase(workspace)} />
            <EnvRow icon={<IconFolder className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />} label="工作区" value={workspace || '未绑定'} />
            <EnvRow icon={<IconUser className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />} label="写入者" value={ROLE_LABELS[role]} />
          </div>
        </div>
        <div className="cx-card cx-enter cx-enter-4 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold">本次验收</h2>
            <span className="text-xs text-[var(--cx-ink-faint)]">由宿主配置</span>
          </div>
          <div className="mt-2">
            <EnvRow icon={<IconShield className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />} label="验证命令" value="Director 自选或上方自定义" />
            <EnvRow icon={<IconShield className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />} label="验收判定" value="以真实收据与 QA 结论为准" />
          </div>
          <p className="mt-2 text-xs leading-relaxed text-[var(--cx-ink-faint)]">浏览器复验、独立 QA 等能力由宿主配置决定；运行过程中的实际证据会实时呈现在时间线与收据中。</p>
        </div>
      </div>
    </div>
  );
}
