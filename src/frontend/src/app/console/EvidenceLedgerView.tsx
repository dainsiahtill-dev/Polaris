import type { ReactElement } from 'react';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { IconCheck, IconX } from './icons';

/** Evidence ledger: every verifier receipt of the current run, nothing hidden. */
export function EvidenceLedgerView({ binding }: { binding: EngineeringRunBinding }): ReactElement {
  const run = binding.projection.snapshot;
  const commands = run?.verification_commands ?? run?.required_checks ?? [];
  if (!run) {
    return (
      <div className="cx-card mx-5 my-4 p-8 text-center">
        <p className="text-sm text-[var(--cx-ink-dim)]">当前工作区没有工程运行。启动运行后，每一张验证收据都会记录在这里。</p>
      </div>
    );
  }
  return (
    <div className="mx-5 my-4 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="cx-eyebrow">RUN · {run.run_id}</p>
          <h1 className="mt-1 text-xl font-bold tracking-tight">证据账本 <span className="ml-2 align-middle text-sm font-medium text-[var(--cx-ink-faint)]">Evidence Ledger</span></h1>
        </div>
        <span className="cx-pill cx-pill--idle">{run.checks.length} 张收据</span>
      </div>
      {run.checks.length === 0 ? (
        <div className="cx-card p-8 text-center text-sm text-[var(--cx-ink-dim)]">运行尚未产出验证收据。</div>
      ) : (
        <div className="cx-card overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-[var(--cx-line)] bg-[#fbfaf5] text-[0.68rem] uppercase tracking-wider text-[var(--cx-ink-faint)]">
                <th className="px-4 py-2.5 font-medium">命令</th>
                <th className="px-4 py-2.5 font-medium">退出码</th>
                <th className="px-4 py-2.5 font-medium">结论</th>
                <th className="px-4 py-2.5 font-medium">输出</th>
              </tr>
            </thead>
            <tbody>
              {run.checks.map((check, index) => {
                const ok = check.ok && check.exit_code === 0;
                return (
                  <tr key={check.call_id} className="border-b border-[var(--cx-line)] align-top last:border-0">
                    <td className="px-4 py-3 font-mono text-xs text-[var(--cx-ink)]">{commands[index] ?? check.tool}</td>
                    <td className="px-4 py-3"><span className={`cx-pill cx-pill--${ok ? 'ok' : check.interrupted ? 'warn' : 'bad'}`}>exit {check.exit_code ?? '—'}{check.interrupted ? ' · 中断' : ''}</span></td>
                    <td className="px-4 py-3">{ok ? <IconCheck className="h-4.5 w-4.5 text-[var(--cx-mint)]" /> : <IconX className="h-4.5 w-4.5 text-[var(--cx-red)]" />}</td>
                    <td className="max-w-md px-4 py-3"><pre className="max-h-36 overflow-y-auto whitespace-pre-wrap font-mono text-[0.7rem] leading-relaxed text-[var(--cx-ink-dim)] cx-scroll">{check.content}</pre></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
