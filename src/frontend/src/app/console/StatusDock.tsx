import type { ReactElement } from 'react';
import { IconDatabase, IconLedger, IconRadio } from './icons';

/** Bottom telemetry dock: transport health and local evidence ledgers. */
export function StatusDock({ connected, receiptCount }: { connected: boolean; receiptCount: number }): ReactElement {
  return (
    <footer className="flex flex-wrap items-center gap-x-6 gap-y-1.5 border-t border-[var(--cx-line)] bg-[#fbfaf5] px-5 py-2" data-testid="status-dock">
      <span className="flex items-center gap-2 font-mono text-[0.68rem] text-[var(--cx-ink-dim)]">
        <IconRadio className="h-3.5 w-3.5" />
        NATS JetStream
        <span className={`cx-dot ${connected ? 'cx-dot--ok' : 'cx-dot--bad'}`} />
        {connected ? '已连接' : '已断开'}
      </span>
      <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
      <span className="flex items-center gap-2 font-mono text-[0.68rem] text-[var(--cx-ink-dim)]">
        <IconDatabase className="h-3.5 w-3.5" />
        sessions.sqlite3
        <span className="cx-dot cx-dot--ok" />
        本地事实源
      </span>
      <span className="hidden h-3 w-px bg-[var(--cx-line-strong)] sm:block" aria-hidden="true" />
      <span className="flex items-center gap-2 font-mono text-[0.68rem] text-[var(--cx-ink-dim)]">
        <IconLedger className="h-3.5 w-3.5" />
        Run Ledger
        <span className={`cx-dot ${receiptCount > 0 ? 'cx-dot--run cx-dot--pulse' : 'cx-dot--idle'}`} />
        {receiptCount > 0 ? `同步中（${receiptCount} 条收据）` : '暂无收据'}
      </span>
    </footer>
  );
}
