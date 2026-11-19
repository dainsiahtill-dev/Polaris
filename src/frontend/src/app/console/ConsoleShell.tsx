import type { ReactElement, ReactNode } from 'react';
import { IconRadio } from './icons';
import { PolarisMark } from './icons';

export interface RailItem {
  id: string;
  label: string;
  icon: ReactNode;
  pressed: boolean;
  onClick: () => void;
}

export interface RailLink {
  id: string;
  label: string;
  icon: ReactNode;
  href: string;
}

/** App frame: dark spruce rail + paper workspace with breadcrumb bar. */
export function ConsoleShell({ workspace, viewLabel, connected, reconnecting, railTop, railMid, railBottom, footerStatus, children }: {
  workspace: string;
  viewLabel: string;
  connected: boolean;
  reconnecting: boolean;
  railTop: RailItem[];
  railMid: RailItem[];
  railBottom: Array<RailItem | RailLink>;
  footerStatus: string;
  children: ReactNode;
}): ReactElement {
  const workspaceBase = workspace.split(/[\\/]/).filter(Boolean).pop() || workspace || '未绑定';
  return (
    <div className="console-scope flex h-full min-h-0 flex-col overflow-hidden md:flex-row">
      <aside className="cx-rail hidden w-56 shrink-0 flex-col md:flex" aria-label="主导航">
        <div className="flex items-center gap-2.5 px-5 pb-5 pt-6">
          <PolarisMark className="h-6 w-6 text-[#dff3e8]" />
          <span className="text-lg font-bold tracking-wide text-[#f2f7f3]">Polaris</span>
        </div>
        <div className="px-4">
          <span className="cx-rail-pill">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="h-4 w-4 shrink-0 opacity-70"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg>
            <span className="truncate">{workspaceBase}</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="ml-auto h-3.5 w-3.5 shrink-0 opacity-50"><path d="m6 9 6 6 6-6" /></svg>
          </span>
        </div>
        <nav className="mt-5 flex-1 space-y-1 overflow-y-auto px-3">
          {railTop.map((item) => (
            <button key={item.id} type="button" aria-pressed={item.pressed} onClick={item.onClick} className="cx-rail-btn">
              {item.icon}
              <span className="truncate">{item.label}</span>
            </button>
          ))}
          {railMid.length > 0 ? <div className="cx-rail-section my-2 pt-2">{railMid.map((item) => (
            <button key={item.id} type="button" aria-pressed={item.pressed} onClick={item.onClick} className="cx-rail-btn">
              {item.icon}
              <span className="truncate">{item.label}</span>
            </button>
          ))}</div> : null}
        </nav>
        <div className="space-y-1 border-t border-[rgba(255,255,255,0.08)] px-3 py-3">
          {railBottom.map((item) => 'href' in item ? (
            <a key={item.id} href={item.href} className="cx-rail-btn">{item.icon}<span className="truncate">{item.label}</span></a>
          ) : (
            <button key={item.id} type="button" aria-pressed={item.pressed} onClick={item.onClick} className="cx-rail-btn">{item.icon}<span className="truncate">{item.label}</span></button>
          ))}
        </div>
        <div className="cx-rail-section space-y-1.5 px-5 py-4">
          <p className="font-mono text-[0.66rem] tracking-wide text-[#8fb8a1]">Polaris / v2</p>
          <p className="flex items-center gap-1.5 text-[0.7rem] text-[#b9d8c6]"><span className={`cx-dot ${connected ? 'cx-dot--light' : 'cx-dot--warn'}`} />工作区：{workspaceBase}</p>
          <p className="flex items-center gap-1.5 text-[0.7rem] text-[#b9d8c6]"><span className={`cx-dot ${connected ? 'cx-dot--light' : 'cx-dot--warn'}`} />状态：{footerStatus}</p>
        </div>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-[var(--cx-line)] bg-[#fbfaf5] px-5 py-2.5">
          <nav aria-label="面包屑" className="flex min-w-0 items-center gap-2 text-sm text-[var(--cx-ink-dim)]">
            <span className="shrink-0">工作区</span>
            <span aria-hidden="true" className="text-[var(--cx-ink-faint)]">/</span>
            <span className="max-w-56 truncate font-medium text-[var(--cx-ink)]" title={workspace}>{workspaceBase}</span>
            <span aria-hidden="true" className="text-[var(--cx-ink-faint)]">/</span>
            <span className="shrink-0">{viewLabel}</span>
          </nav>
          <p role="status" className="flex items-center gap-2 font-mono text-[0.7rem] text-[var(--cx-ink-dim)]">
            <IconRadio className="h-4 w-4" />
            <span className={`cx-dot ${connected ? 'cx-dot--ok' : reconnecting ? 'cx-dot--warn' : 'cx-dot--bad'}`} />
            {connected ? '实时连接已建立' : reconnecting ? '正在重新连接' : '实时连接已断开'}
          </p>
        </header>
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
