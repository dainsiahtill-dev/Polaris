import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { contextRefOf, eventKindLabel, eventTone, formatClock, receiptSummary, toolGlyph } from './format';
import { IconChevronRight, IconFile, IconFolder, IconGlobe, IconSearch, IconTerminal } from './icons';

const GLYPHS = {
  folder: IconFolder,
  file: IconFile,
  terminal: IconTerminal,
  browser: IconGlobe,
  search: IconSearch,
  generic: IconFile,
} as const;

function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);
}

/** Live receipt stream: one card per tool call / observation. */
export function ToolTimeline({ events, onOpenContext, emptyHint }: {
  events: EngineeringEvent[];
  onOpenContext: (ref: string, role: string) => void;
  emptyHint: string;
}): ReactElement {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const shown = useMemo(() => events.slice(-100).reverse(), [events]);
  return (
    <div className="space-y-2.5">
      {shown.length === 0 ? <p className="px-1 py-6 text-center text-sm text-[var(--cx-ink-faint)]">{emptyHint}</p> : null}
      {shown.map((event) => {
        const tone = eventTone(event);
        const glyphKind = toolGlyph(typeof event.payload.tool === 'string' ? event.payload.tool : undefined);
        const Glyph = GLYPHS[glyphKind];
        const ref = contextRefOf(event);
        const isOpen = expanded[event.seq] === true;
        const rawContent = typeof event.payload.content === 'string' ? event.payload.content : text(event.payload);
        const content = rawContent.length > 600 && !isOpen ? `${rawContent.slice(0, 600)}…` : rawContent;
        return (
          <article key={event.seq} data-tone={tone} className={`cx-receipt cx-receipt--${tone}`}>
            <span className="cx-receipt-glyph mt-0.5"><Glyph className="h-4.5 w-4.5" /></span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-3">
                <p className="truncate text-sm font-semibold">{receiptSummary(event)}</p>
                <span className="cx-mono shrink-0 text-[0.68rem] text-[var(--cx-ink-faint)]">{formatClock(event.created_at)}</span>
              </div>
              <p className="mt-0.5 text-xs text-[var(--cx-ink-dim)]">{event.role} · {eventKindLabel(event.kind)}</p>
              {rawContent ? (
                <button type="button" onClick={() => setExpanded((previous) => ({ ...previous, [event.seq]: !isOpen }))} className="mt-2 block w-full text-left">
                  {glyphKind === 'terminal' ? (
                    <span className="cx-terminal block">
                      <span className="text-[#8fb8a1]">$ </span>{content.split('\n')[0]}
                      {content.includes('\n') ? <>{'\n'}{content.split('\n').slice(1).join('\n')}</> : null}
                      {tone === 'ok' ? <span className="cx-terminal-ok">{'\n'}exit 0</span> : tone === 'bad' ? <span className="cx-terminal-bad">{'\n'}failed</span> : null}
                    </span>
                  ) : (
                    <span className="block truncate rounded-md bg-[#f4f2ea] px-2.5 py-1.5 font-mono text-[0.72rem] text-[var(--cx-ink-dim)]">{content}</span>
                  )}
                </button>
              ) : null}
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {ref ? (
                  <button type="button" onClick={() => onOpenContext(ref, event.role)} className="rounded-md border border-[#c8dfe6] bg-[#eef6f7] px-2 py-1 font-mono text-[0.66rem] text-[var(--cx-cyan)] hover:border-[#9ec9d2]">
                    查看最终请求 · {ref.slice(0, 8)}…
                  </button>
                ) : null}
                {event.payload.truncated_fields !== undefined && Array.isArray(event.payload.truncated_fields) && event.payload.truncated_fields.length > 0 ? (
                  <span className="text-[0.66rem] text-[var(--cx-amber)]">输出已截断，完整记录在运行快照中</span>
                ) : null}
              </div>
            </div>
            <span className="mt-1 text-[var(--cx-ink-faint)]"><IconChevronRight className="h-4 w-4" /></span>
          </article>
        );
      })}
    </div>
  );
}
