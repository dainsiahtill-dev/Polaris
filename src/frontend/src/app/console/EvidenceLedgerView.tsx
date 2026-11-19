import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringCheck } from '@/services/api.types';
import type { EngineeringRunBinding } from '@/hooks/useEngineeringRun';
import { firstFilePath, outcomeTone, receiptId, runShortId, toolLabel } from './format';
import { IconCheck, IconCopy, IconGlobe, IconInfo, IconTerminal, IconX } from './icons';

function CandidateCheckRow({ name, command, state, receipt, selected, onClick }: {
  name: string;
  command: string;
  state: 'ok' | 'bad' | 'missing';
  receipt: string;
  selected: boolean;
  onClick: () => void;
}): ReactElement {
  return (
    <button type="button" data-selected={selected} onClick={onClick} className={`flex w-full items-center gap-3 border-b border-[var(--cx-line)] px-4 py-3 text-left transition-colors last:border-0 ${selected ? 'bg-[var(--cx-teal-mist)]' : 'hover:bg-[#f7f8fa]'}`}>
      <span className="text-[var(--cx-ink-dim)]">{state === 'missing' ? <IconGlobe className="h-4.5 w-4.5" /> : <IconTerminal className="h-4.5 w-4.5" />}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-bold text-[var(--cx-ink)]">{name}</span>
        <span className="block truncate font-mono text-xs text-[var(--cx-ink-dim)]">{command}</span>
      </span>
      {state === 'ok' ? <span className="cx-pill cx-pill--ok"><IconCheck className="h-3.5 w-3.5" />通过</span> : state === 'bad' ? <span className="cx-pill cx-pill--bad"><IconX className="h-3.5 w-3.5" />失败</span> : <span className="cx-pill cx-pill--warn">缺少证据</span>}
      <span className="cx-mono w-20 text-right text-xs text-[var(--cx-ink-faint)]">{receipt}</span>
    </button>
  );
}

/** ⑧ Evidence ledger workbench: candidate checks and receipt details. */
export function EvidenceLedgerView({ binding }: { binding: EngineeringRunBinding }): ReactElement {
  const run = binding.projection.snapshot;
  const commands = run?.verification_commands ?? run?.required_checks ?? [];
  const [selectedIndex, setSelectedIndex] = useState(0);
  const rows = useMemo(() => {
    if (!run) return [];
    const list: Array<{ check: EngineeringCheck | null; command: string; index: number }> = run.checks.map((check, index) => ({ check, command: commands[index] ?? check.tool, index }));
    commands.forEach((command, commandIndex) => {
      if (!run.checks[commandIndex]) list.push({ check: null, command, index: run.checks.length + commandIndex });
    });
    return list;
  }, [run, commands]);
  const selectedRow = rows[Math.min(selectedIndex, Math.max(0, rows.length - 1))];
  if (!run) {
    return (
      <div className="mx-5 my-4">
        <div className="cx-card p-8 text-center">
          <p className="text-sm text-[var(--cx-ink-dim)]">当前工作区没有工程运行。启动运行后，每一张验证收据都会记录在这里。</p>
        </div>
      </div>
    );
  }
  const tone = outcomeTone(run.project_outcome);
  const shortId = runShortId(run.session_id).replace('RUN-', '').toLowerCase();
  return (
    <div className="mx-5 my-4 flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">证据账本</h1>
          <p className="mt-1 flex items-center gap-2 text-sm text-[var(--cx-ink-dim)]">
            <span className="inline-flex items-center gap-1.5"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4"><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></svg>运行</span>
            <span className="cx-mono text-xs">{shortId}</span>
            <span className="text-[var(--cx-ink-faint)]">· 当前候选的全部证据</span>
          </p>
        </div>
        <span className={`cx-pill cx-pill--${tone === 'idle' ? 'warn' : tone} px-3 py-1.5 text-sm`}>项目{run.project_outcome === 'accepted' ? '已验收' : run.project_outcome === 'rejected' ? '已拒绝' : '未验收'}</span>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex items-center gap-2 text-sm font-bold"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4.5 w-4.5"><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></svg>候选版本</span>
        <span className="flex items-center gap-2.5 rounded-xl border border-[var(--cx-teal)] bg-[var(--cx-teal-mist)] px-4 py-2.5">
          <span aria-hidden="true" className="flex h-4 w-4 items-center justify-center rounded-full border-2 border-[var(--cx-teal)]"><span className="h-1.5 w-1.5 rounded-full bg-[var(--cx-teal)]" /></span>
          <span className="text-sm font-bold">当前候选 <span className="cx-mono ml-1 text-xs">{shortId}</span></span>
        </span>
        <span className="text-xs text-[var(--cx-ink-faint)]">历史候选保留在运行快照中。</span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-12">
        <section className="cx-card flex min-h-0 flex-col overflow-hidden xl:col-span-7">
          <div className="cx-card-head"><h2 className="cx-card-title">候选检查</h2><span className="cx-pill cx-pill--idle">{rows.length} 项</span></div>
          <div className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-[var(--cx-line)] bg-[#f7f8fa] px-4 py-2 text-xs font-medium text-[var(--cx-ink-faint)]">
            <span>检查项</span><span>证据状态</span><span className="w-20 text-right">收据</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto cx-scroll">
            {rows.map((row) => {
              const state = row.check ? (row.check.ok && row.check.exit_code === 0 ? 'ok' : 'bad') : 'missing';
              const toolName = row.check?.tool || row.command.split(' ')[0] || 'check';
              return (
                <CandidateCheckRow
                  key={`${row.index}-${row.command}`}
                  name={`${toolLabel(toolName)} · ${toolName}`}
                  command={row.command}
                  state={state}
                  receipt={row.check ? receiptId(row.index + 1) : '—'}
                  selected={selectedRow === row}
                  onClick={() => setSelectedIndex(rows.indexOf(row))}
                />
              );
            })}
            {rows.length === 0 ? <p className="px-4 py-8 text-center text-sm text-[var(--cx-ink-faint)]">当前候选尚未产出检查。</p> : null}
          </div>
          {rows.some((row) => !row.check) ? (
            <p className="flex items-center gap-2 border-t border-[var(--cx-line)] bg-[#fbfbfc] px-4 py-2.5 text-xs text-[var(--cx-ink-dim)]"><IconInfo className="h-4 w-4 text-[var(--cx-amber)]" />标记「缺少证据」的命令已声明但尚未产出收据。</p>
          ) : null}
        </section>
        <section className="cx-card flex min-h-0 flex-col overflow-hidden xl:col-span-5">
          <div className="cx-card-head">
            <h2 className="cx-card-title">收据详情</h2>
            {selectedRow?.check ? (
              <span className="flex items-center gap-2">
                <span className="cx-mono text-xs text-[var(--cx-ink-dim)]">{receiptId(rows.indexOf(selectedRow) + 1)}</span>
                <span className={`cx-pill ${selectedRow.check.ok && selectedRow.check.exit_code === 0 ? 'cx-pill--ok' : 'cx-pill--bad'}`}>exit {selectedRow.check.exit_code ?? '—'}</span>
              </span>
            ) : <span className="cx-pill cx-pill--warn">缺少证据</span>}
          </div>
          {selectedRow?.check ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="border-b border-[var(--cx-line)]">
                <DetailRow icon={<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4.5 w-4.5"><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></svg>} label="候选" value={shortId} />
                <DetailRow icon={<IconTerminal className="h-4.5 w-4.5" />} label="工具" value={selectedRow.check.tool} mono />
                <DetailRow icon={<IconCopy className="h-4.5 w-4.5" />} label="关联文件" value={firstFilePath(selectedRow.check.content) ?? '—'} mono />
              </div>
              <div className="flex min-h-0 flex-1 flex-col p-4">
                <p className="text-sm font-bold">原始输出</p>
                <pre className="cx-terminal mt-2 min-h-0 flex-1 overflow-y-auto cx-scroll">{selectedRow.check.content || '（无输出）'}</pre>
              </div>
            </div>
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
              <IconInfo className="h-6 w-6 text-[var(--cx-amber)]" />
              <p className="text-sm text-[var(--cx-ink-dim)]">该检查尚未产出当前候选的收据。</p>
            </div>
          )}
        </section>
      </div>
      <footer className="flex flex-wrap items-center gap-x-6 gap-y-1.5 border-t border-[var(--cx-line)] pt-3 font-mono text-[0.68rem] text-[var(--cx-ink-dim)]">
        <span>工程运行 · sessions.sqlite3</span>
        <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
        <span>工具收据 · ReceiptStore</span>
      </footer>
    </div>
  );
}

function DetailRow({ icon, label, value, mono }: { icon: ReactElement; label: string; value: string; mono?: boolean }): ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5">
      <span className="flex items-center gap-2.5 text-sm text-[var(--cx-ink-dim)]">{icon}{label}</span>
      <span className={`truncate text-xs text-[var(--cx-ink)] ${mono ? 'cx-mono' : ''}`}>{value}</span>
    </div>
  );
}
