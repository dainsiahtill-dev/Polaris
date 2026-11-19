import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { contextRefOf } from './format';
import { IconCheck, IconCopy } from './icons';

/** Latest available context snapshot ref, with copy-to-clipboard. */
export function ContextRefCard({ events, onOpen }: { events: EngineeringEvent[]; onOpen: (ref: string) => void }): ReactElement {
  const [copied, setCopied] = useState(false);
  const latestRef = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const ref = contextRefOf(events[index]);
      if (ref) return ref;
    }
    return null;
  }, [events]);
  const copy = async () => {
    if (!latestRef) return;
    try {
      await navigator.clipboard.writeText(latestRef);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <div className="space-y-2.5">
      <button type="button" onClick={() => latestRef && onOpen(latestRef)} disabled={!latestRef} className="block font-mono text-[0.78rem] text-[var(--cx-cyan)] underline decoration-dotted underline-offset-4 hover:text-[#155e6d] disabled:no-underline disabled:text-[var(--cx-ink-faint)]">
        context_snapshot_ref
      </button>
      <div className="flex items-center gap-2">
        <code className={`flex-1 truncate rounded-lg border px-2.5 py-2 font-mono text-[0.72rem] ${latestRef ? 'border-[var(--cx-line)] bg-[#fbfaf5] text-[var(--cx-ink)]' : 'border-dashed border-[var(--cx-line-strong)] text-[var(--cx-ink-faint)]'}`}>
          {latestRef || '暂无可用快照'}
        </code>
        <button type="button" onClick={() => void copy()} disabled={!latestRef} aria-label="复制快照引用" className="cx-icon-btn disabled:opacity-40">
          {copied ? <IconCheck className="h-4 w-4 text-[var(--cx-mint)]" /> : <IconCopy className="h-4 w-4" />}
        </button>
      </div>
      <p className="text-[0.68rem] leading-relaxed text-[var(--cx-ink-faint)]">24 位十六进制 key；点击可审阅该次 LLM 调用的最终完整请求上下文。</p>
    </div>
  );
}
