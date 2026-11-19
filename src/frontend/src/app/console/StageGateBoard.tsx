import type { ReactElement } from 'react';
import type { EngineeringCheck } from '@/services/api.types';
import { loopInterventions, writesSinceVerification, type Tone } from './format';
import type { EngineeringEvent } from '@/services/api.types';
import { IconCheck, IconClock, IconFlag, IconGauge, IconX } from './icons';

const GATE_THRESHOLD = 5;

function GateRow({ label, value, icon, tone }: { label: string; value: string; icon: ReactElement; tone?: Tone }): ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 rounded-xl border border-[var(--cx-line)] bg-[#fdfcf8] px-3.5 py-3">
      <span className="flex items-center gap-2.5 text-sm text-[var(--cx-ink)]">
        <span className="text-[var(--cx-green)]">{icon}</span>
        {label}
      </span>
      <span className={`cx-pill ${tone ? `cx-pill--${tone}` : 'cx-pill--idle'}`}>{value}</span>
    </div>
  );
}

/** Stage-gate dashboard: write-budget meter, loop interventions, verification trio. */
export function StageGateBoard({ events, commands, checks }: {
  events: EngineeringEvent[];
  commands: string[];
  checks: EngineeringCheck[];
}): ReactElement {
  const gate = writesSinceVerification(events, GATE_THRESHOLD);
  const loops = loopInterventions(events);
  const findCheck = (keyword: RegExp): EngineeringCheck | undefined =>
    checks.find((check, index) => keyword.test(check.tool) || keyword.test(commands[index] || ''));
  const trio = [
    { key: 'build', label: 'build', command: commands.find((command) => /build/i.test(command)), check: findCheck(/build/i) },
    { key: 'test', label: 'test', command: commands.find((command) => /test/i.test(command)), check: findCheck(/test/i) },
    { key: 'lint', label: 'lint', command: commands.find((command) => /lint/i.test(command)), check: findCheck(/lint/i) },
  ];
  return (
    <div className="space-y-2.5">
      <div className="rounded-xl border border-[var(--cx-line)] bg-[#fdfcf8] px-3.5 py-3">
        <div className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-2.5 text-sm"><span className="text-[var(--cx-green)]"><IconGauge className="h-4.5 w-4.5" /></span>写入后验证</span>
          <span className="cx-mono text-xs text-[var(--cx-ink-dim)]">{Math.min(gate.count, gate.threshold)} / {gate.threshold}</span>
        </div>
        <div className="cx-meter mt-2.5" aria-hidden="true">
          {Array.from({ length: gate.threshold }, (_, index) => (
            <span key={index} className={`cx-meter-seg ${index < gate.count ? 'cx-meter-seg--fill' : ''}`} />
          ))}
        </div>
        <p className="mt-2 text-[0.68rem] text-[var(--cx-ink-faint)]">写满 {gate.threshold} 次文件操作后，阶段门禁强制插入一次真实验证。</p>
      </div>
      <GateRow label="循环干预" value={`${loops} 次`} icon={<IconFlag className="h-4.5 w-4.5" />} tone={loops > 0 ? 'warn' : 'ok'} />
      <div className="grid grid-cols-3 gap-2">
        {trio.map((entry) => {
          const state = entry.check ? (entry.check.ok && entry.check.exit_code === 0 ? 'ok' : 'bad') : entry.command ? 'info' : 'idle';
          return (
            <div key={entry.key} data-testid={`stage-gate-${entry.key}`} className="flex flex-col items-center gap-1 rounded-xl border border-[var(--cx-line)] bg-[#fdfcf8] px-2 py-3">
              {state === 'ok' ? <IconCheck className="h-4.5 w-4.5 text-[var(--cx-mint)]" /> : state === 'bad' ? <IconX className="h-4.5 w-4.5 text-[var(--cx-red)]" /> : <IconClock className="h-4.5 w-4.5 text-[var(--cx-ink-faint)]" />}
              <span className="text-xs font-semibold">{entry.label}</span>
              <span className={`cx-pill cx-pill--${state === 'info' ? 'info' : state}`}>{state === 'ok' ? '通过' : state === 'bad' ? '失败' : entry.command ? '待执行' : '未声明'}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
