import { useMemo, type ReactElement } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { contextRefOf, eventKindLabel, eventTone, formatClock, receiptId, receiptSummary, toolGlyph, toolLabel } from './format';
import { IconCheck, IconChevronRight, IconFile, IconFolder, IconGlobe, IconSearch, IconTerminal } from './icons';

const GLYPHS = {
  folder: IconFolder,
  file: IconFile,
  terminal: IconTerminal,
  browser: IconGlobe,
  search: IconSearch,
  generic: IconFile,
} as const;

/** Tool-and-run journal: selectable rows, one receipt each. */
export function ToolTimeline({ events, selectedSeq, onSelect, emptyHint }: {
  events: EngineeringEvent[];
  selectedSeq: number | null;
  onSelect: (event: EngineeringEvent) => void;
  emptyHint: string;
}): ReactElement {
  const shown = useMemo(() => events.slice(-100), [events]);
  return (
    <div>
      {shown.length === 0 ? <p className="px-4 py-8 text-center text-sm text-[var(--cx-ink-faint)]">{emptyHint}</p> : null}
      {shown.map((event) => {
        const tone = eventTone(event);
        const glyphKind = toolGlyph(typeof event.payload.tool === 'string' ? event.payload.tool : undefined);
        const Glyph = GLYPHS[glyphKind];
        const tool = typeof event.payload.tool === 'string' ? event.payload.tool : event.kind;
        const summary = receiptSummary(event);
        const ref = contextRefOf(event);
        return (
          <button key={event.seq} type="button" data-testid={`timeline-row-${event.seq}`} data-selected={selectedSeq === event.seq} data-tone={tone} className="cx-tl-row" onClick={() => onSelect(event)}>
            <span className="cx-tl-time">{formatClock(event.created_at)}</span>
            <span className="cx-tl-track" aria-hidden="true">
              <span className={`cx-tl-dot cx-tl-dot--${tone}`} />
              <span className="cx-tl-line" />
            </span>
            <span className="cx-tl-glyph"><Glyph className="h-4.5 w-4.5" /></span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center justify-between gap-3">
                <span className="truncate text-sm font-semibold text-[var(--cx-ink)]">{tool}</span>
                {tone === 'ok' ? <IconCheck className="h-4.5 w-4.5 shrink-0 text-[var(--cx-teal-bright)]" /> : tone === 'bad' ? <span className="cx-pill cx-pill--bad">失败</span> : <span className="cx-pill cx-pill--idle">{eventKindLabel(event.kind)}</span>}
              </span>
              <span className="mt-0.5 flex items-center justify-between gap-3">
                <span className="truncate text-xs text-[var(--cx-ink-dim)]">{toolLabel(tool)} · {summary}</span>
                <span className="cx-mono shrink-0 text-[0.64rem] text-[var(--cx-ink-faint)]">{receiptId(event.seq)}</span>
              </span>
              {ref ? <span className="mt-1 block font-mono text-[0.64rem] text-[#1f7a8c]">最终请求 · {ref.slice(0, 8)}…</span> : null}
            </span>
            <IconChevronRight className="mt-1 h-4 w-4 shrink-0 text-[var(--cx-ink-faint)]" />
          </button>
        );
      })}
    </div>
  );
}
