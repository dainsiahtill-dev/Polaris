import { useMemo, useState, type ReactElement } from 'react';
import type { EngineeringEvent } from '@/services/api.types';
import { contextRefOf, formatClock, toolGlyph, toolLabel } from './format';
import {
  IconCheck, IconChevronRight, IconClapper, IconFile, IconFlag, IconFolder, IconGauge,
  IconGlobe, IconInfo, IconSearch, IconShield, IconTerminal,
} from './icons';

const GLYPHS = {
  folder: IconFolder,
  file: IconFile,
  terminal: IconTerminal,
  browser: IconGlobe,
  search: IconSearch,
  generic: IconFile,
} as const;

/** Human-readable story beats derived from engineering events. */
export type StoryKind =
  | 'narration'      // role text (message)
  | 'tool'           // tool_observed
  | 'stage_gate'     // stage_gate_enforced
  | 'loop_guard'     // loop_detected
  | 'visual_qa'      // visual_observation_completed
  | 'llm_call'       // llm_request_observed
  | 'fold'           // context_projection_folded
  | 'advisor'        // semantic_advice_completed
  | 'epoch'          // epoch_card_completed
  | 'verification';  // verification_started / completed

const DISPLAY_KINDS = new Set([
  'message', 'tool_observed', 'stage_gate_enforced', 'loop_detected',
  'visual_observation_completed', 'llm_request_observed', 'context_projection_folded', 'semantic_advice_completed', 'epoch_card_completed',
  'verification_started', 'verification_completed',
]);

export function storyKindOf(event: EngineeringEvent): StoryKind | null {
  switch (event.kind) {
    case 'message': return 'narration';
    case 'tool_observed': return 'tool';
    case 'stage_gate_enforced': return 'stage_gate';
    case 'loop_detected': return 'loop_guard';
    case 'visual_observation_completed': return 'visual_qa';
    case 'llm_request_observed': return 'llm_call';
    case 'context_projection_folded': return 'fold';
    case 'semantic_advice_completed': return 'advisor';
    case 'epoch_card_completed': return 'epoch';
    case 'verification_started':
    case 'verification_completed': return 'verification';
    default: return null;
  }
}

function textOf(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function narrationText(event: EngineeringEvent): string {
  const direct = textOf(event.payload.text);
  if (direct) return direct;
  const message = event.payload.message;
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content;
    if (typeof content === 'string') return content;
  }
  return '';
}

function toolSummary(event: EngineeringEvent): string {
  const payload = event.payload;
  const path = textOf(payload.path) || textOf(payload.file_path);
  const command = textOf(payload.command) || (Array.isArray(payload.argv) ? (payload.argv as unknown[]).join(' ') : '');
  const tool = textOf(payload.tool) || event.kind;
  if (path) return path;
  if (command) return command;
  return tool;
}

function beatText(event: EngineeringEvent): string {
  const payload = event.payload;
  switch (event.kind) {
    case 'stage_gate_enforced':
      return `阶段门禁：已连续写入 ${String(payload.writes_since_verification ?? '若干')} 次，先验证再继续。`;
    case 'loop_detected':
      return `检测到重复动作（连续 ${String(payload.consecutive_identical_turns ?? '?')} 次相同），已注入修正指引。`;
    case 'visual_observation_completed': {
      const binding = payload.input_binding && typeof payload.input_binding === 'object' ? payload.input_binding as Record<string, unknown> : {};
      const mb = typeof binding.bytes === 'number' ? ` · ${(binding.bytes / 1024).toFixed(0)} KB` : '';
      return `浏览器渲染观测完成${mb}，画面与预期比对中。`;
    }
    case 'verification_started':
      return '验证开始：对当前产物执行检查。';
    case 'verification_completed':
      return payload.verification === 'passed' ? '验证通过。' : '验证未通过，继续修复。';
    default:
      return event.kind;
  }
}

/**
 * One model turn is one provider request plus every tool call it dispatched.
 * The timeline groups by `turn_seq` so a turn's parallel tool calls read as one
 * unit instead of unrelated rows.
 */
type TurnGroup = { key: string; turn: number | null; events: EngineeringEvent[] };

function groupByTurn(events: EngineeringEvent[]): TurnGroup[] {
  const groups: TurnGroup[] = [];
  for (const event of events) {
    const turn = typeof event.payload.turn_seq === 'number' ? event.payload.turn_seq : null;
    if (turn !== null) {
      const last = groups[groups.length - 1];
      if (last && last.turn === turn) {
        last.events.push(event);
        continue;
      }
    }
    groups.push({ key: turn === null ? `seq-${event.seq}` : `turn-${turn}`, turn, events: [event] });
  }
  return groups;
}

function FoldBadge({ reason }: { reason: string }) {
  return (
    <span
      className="fold-badge"
      data-testid="fold-marker"
      role="status"
      aria-label={`已智能折叠：${reason}`}
      title={reason}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '2px 7px',
        borderRadius: 999,
        color: '#c4b5fd',
        background: 'linear-gradient(110deg, rgba(98,80,214,0.25), rgba(27,175,122,0.18), rgba(98,80,214,0.25))',
        backgroundSize: '220% 100%',
        border: '1px solid rgba(144,133,233,0.55)',
        boxShadow: '0 0 12px rgba(144,133,233,0.22)',
        fontSize: 9,
        fontWeight: 650,
        letterSpacing: '.02em',
        animation: 'foldEnter .35s ease-out, foldShimmer 2.8s linear infinite',
      }}
    >
      <IconChevronRight className="h-3 w-3 rotate-90" aria-hidden="true" />
      {reason}
    </span>
  );
}

function foldMeta(event: EngineeringEvent): { folded: boolean; reason: string } {
  const payload = event.payload as Record<string, unknown>;
  const direct = payload.fold_state === 'FOLDED';
  const nested = ['file_card', 'command_card'].some((key) => {
    const card = payload[key] as Record<string, unknown> | undefined;
    return card?.fold_state === 'FOLDED';
  });
  const nestedReason = ['file_card', 'command_card'].map((key) => {
    const card = payload[key] as Record<string, unknown> | undefined;
    return textOf(card?.fold_reason_code) || textOf(card?.fold_reason) || '';
  }).find(Boolean) || '';
  const reason = nestedReason || textOf(payload.fold_reason_code) || textOf(payload.fold_reason) || 'FOLDED';
  return { folded: direct || nested, reason };
}

const TURN_STYLE = {
  border: '1px solid var(--cx-line-strong)',
  background: 'linear-gradient(180deg, rgba(31,122,140,.06), rgba(31,122,140,.015))',
} as const;

/** The live run story: turns boxed, narration first, tools as annotations, platform beats in between. */
export function ToolTimeline({ events, selectedSeq, onSelect, onOpenContext, emptyHint }: {
  events: EngineeringEvent[];
  selectedSeq: number | null;
  onSelect: (event: EngineeringEvent) => void;
  onOpenContext: (ref: string, role: string) => void;
  emptyHint: string;
}): ReactElement {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const story = useMemo(() => events.filter((event) => DISPLAY_KINDS.has(event.kind)).slice(-120).reverse(), [events]);
  const groups = useMemo(() => groupByTurn(story), [story]);

  const renderEvent = (event: EngineeringEvent): ReactElement => {
    const kind = storyKindOf(event);
    if (kind === 'narration') {
      const author = textOf(event.payload.author_role);
      if (author && author !== 'assistant') return <span key={event.seq} />;
      const text = narrationText(event);
      if (!text.trim()) return <span key={event.seq} />;
      const isOpen = expanded[event.seq] === true;
      const shown = isOpen ? text : text.length > 500 ? `${text.slice(0, 500)}…` : text;
      return (
        <article key={event.seq} data-testid={`story-narration-${event.seq}`} className="rounded-xl border border-[var(--cx-line)] bg-[#fbfaf6] px-3.5 py-3">
          <div className="flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-[var(--cx-teal-strong)]"><IconClapper className="h-3.5 w-3.5" />{event.role}</p>
            <span className="cx-mono text-[0.64rem] text-[var(--cx-ink-faint)]">{formatClock(event.created_at)}</span>
          </div>
          <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-[var(--cx-ink)]">{shown}</p>
          {text.length > 500 ? (
            <button type="button" className="mt-1 text-xs text-[var(--cx-teal-strong)] hover:underline" onClick={() => setExpanded((p) => ({ ...p, [event.seq]: !isOpen }))}>{isOpen ? '收起' : '展开全部'}</button>
          ) : null}
        </article>
      );
    }
    if (kind === 'epoch') {
      return (
        <article key={event.seq} data-testid={`story-epoch-${event.seq}`} className="rounded-xl border border-[rgba(213,81,129,.32)] bg-[rgba(213,81,129,.06)] px-3 py-2.5">
          <div className="flex items-center gap-2">
            <IconClapper className="h-4 w-4 text-[#d55181]" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-[var(--cx-ink)]">Devourer 吞噬兽 · {textOf(event.payload.id)}</p>
              <p className="mt-0.5 line-clamp-3 text-[0.7rem] text-[var(--cx-ink-dim)]">{textOf(event.payload.summary)}</p>
            </div>
          </div>
        </article>
      );
    }
    if (kind === 'advisor') {
      const summaries = Array.isArray(event.payload.summaries) ? event.payload.summaries : [];
      // The host emits one entry per endpoint: {validation_result, result:{node_id,summary}}.
      // Reading node_id/summary at the top level always rendered "（无摘要）".
      const lines = summaries.flatMap((item) => {
        const record = item as Record<string, unknown>;
        if (record.validation_result && record.validation_result !== 'VALID') return [];
        const result = (
          record.result && typeof record.result === 'object' ? record.result : record
        ) as Record<string, unknown>;
        const node = textOf(result.node_id) || '节点';
        const summary = textOf(result.summary).slice(0, 120) || '（无摘要）';
        return [`${node} → ${summary}`];
      });
      return (
        <article key={event.seq} data-testid={`story-advisor-${event.seq}`} className="rounded-xl border border-[rgba(31,122,140,.28)] bg-[rgba(31,122,140,.05)] px-3 py-2">
          <div className="flex items-center gap-2">
            <IconGauge className="h-4 w-4 text-[#1f7a8c]" />
            <p className="text-xs font-semibold text-[var(--cx-ink)]">
              蒸馏器 · 蒸馏内容{lines.length ? ` · ${lines.length} 个节点` : ''}
            </p>
          </div>
          {lines.length === 0 ? (
            <p className="mt-1 text-[0.7rem] text-[var(--cx-ink-dim)]">暂无蒸馏结果</p>
          ) : (
            <ul className="mt-1 space-y-0.5">
              {lines.map((line, index) => (
                <li key={index} className="text-[0.7rem] leading-relaxed text-[var(--cx-ink)]">{line}</li>
              ))}
            </ul>
          )}
        </article>
      );
    }
    if (kind === 'fold') {
      const fold = foldMeta(event);
      const path = textOf(event.payload.path) || textOf(event.payload.projection_ref);
      const summary = textOf(event.payload.summary);
      const activityAge = typeof event.payload.activity_age === 'number' ? event.payload.activity_age : null;
      return (
        <article key={event.seq} data-testid={`story-fold-${event.seq}`} className="fold-event rounded-xl border px-3 py-2.5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[rgba(98,80,214,0.14)] text-[#9085e9]">
              <IconChevronRight className="h-4 w-4 rotate-90" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-[var(--cx-ink)]">智能折叠已触发</p>
              <p className="truncate text-[0.7rem] text-[var(--cx-ink-dim)]">
                {activityAge !== null ? `${activityAge} 轮前 · ` : ''}{path}
              </p>
              {summary ? <p className="mt-0.5 line-clamp-2 text-[0.7rem] text-[var(--cx-ink-dim)]">{summary}</p> : null}
            </div>
            <FoldBadge reason={fold.reason} />
          </div>
        </article>
      );
    }
    if (kind === 'tool') {
      const payload = event.payload;
      const tool = textOf(payload.tool) || event.kind;
      const ok = payload.ok !== false;
      const fold = foldMeta(event);
      const glyphKind = toolGlyph(tool);
      const Glyph = GLYPHS[glyphKind];
      const images = Array.isArray(payload.images) ? payload.images as Record<string, unknown>[] : [];
      if (images.length > 0) {
        return (
          <article key={event.seq} data-testid={`story-tool-${event.seq}`} className="rounded-xl border border-white/[0.07] bg-black/20 p-2">
            <button type="button" className="flex w-full items-center gap-2 text-left" onClick={() => onSelect(event)}>
              <span className="cx-tl-glyph !h-7 !w-7"><Glyph className="h-3.5 w-3.5" /></span>
              <span className="min-w-0 flex-1 truncate text-[0.8rem] text-[var(--cx-ink)]">
                {toolLabel(tool)} · {toolSummary(event)}
              </span>
              {ok ? <IconCheck className="h-4 w-4 text-[var(--cx-teal-bright)]" /> : <span className="cx-pill cx-pill--bad">失败</span>}
            </button>
            <div className="mt-2 flex flex-wrap gap-2">
              {images.slice(0, 4).map((image, index) => {
                const source = textOf(image.data_base64);
                return (
                  <img
                    key={`${event.seq}-${index}`}
                    src={`data:${textOf(image.media_type) || 'image/jpeg'};base64,${source}`}
                    alt={`浏览器截图 ${index + 1}`}
                    data-testid={`browser-shot-${event.seq}-${index}`}
                    className="h-24 rounded-lg border border-white/10 object-cover"
                  />
                );
              })}
            </div>
          </article>
        );
      }
      return (
        <button key={event.seq} type="button" data-testid={`story-tool-${event.seq}`} data-selected={selectedSeq === event.seq} className="cx-tl-row !py-2.5" onClick={() => onSelect(event)}>
          <span className="cx-tl-time">{formatClock(event.created_at)}</span>
          <span className="cx-tl-glyph !h-7 !w-7"><Glyph className="h-3.5 w-3.5" /></span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center justify-between gap-3">
              <span className="truncate text-[0.82rem] font-medium text-[var(--cx-ink)]">{toolLabel(tool)} · {toolSummary(event)}</span>
              {fold.folded ? <FoldBadge reason={fold.reason} /> : null}
              {ok ? <IconCheck className="h-4 w-4 shrink-0 text-[var(--cx-teal-bright)]" /> : <span className="cx-pill cx-pill--bad">失败</span>}
            </span>
          </span>
          <IconChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--cx-ink-faint)]" />
        </button>
      );
    }
    if (kind === 'llm_call') {
      const payload = event.payload;
      const audit = payload.audit && typeof payload.audit === 'object' ? payload.audit as Record<string, unknown> : {};
      const tokens = typeof audit.final_request_token_estimate === 'number' ? `${audit.final_request_token_estimate.toLocaleString()} tok` : '';
      const model = textOf(payload.model);
      const ref = contextRefOf(event);
      return (
        <div key={event.seq} data-testid={`story-llm-${event.seq}`} className="flex items-center gap-2.5 rounded-lg border border-dashed border-[var(--cx-line-strong)] px-3 py-2">
          <IconGauge className="h-4 w-4 shrink-0 text-[var(--cx-ink-faint)]" />
          <p className="min-w-0 flex-1 truncate text-xs text-[var(--cx-ink-dim)]">模型请求{model ? ` · ${model}` : ''}{tokens ? ` · 约 ${tokens}` : ''}</p>
          {ref ? (
            <button type="button" className="shrink-0 font-mono text-[0.66rem] text-[#1f7a8c] hover:text-[#155e6d]" onClick={() => onOpenContext(ref, event.role)}>查看最终请求 · {ref.slice(0, 8)}…</button>
          ) : <span className="shrink-0 text-[0.66rem] text-[var(--cx-ink-faint)]">快照不可用</span>}
        </div>
      );
    }
    const beatIcon = kind === 'stage_gate' ? <IconGauge className="h-4 w-4" />
      : kind === 'loop_guard' ? <IconFlag className="h-4 w-4" />
        : kind === 'visual_qa' ? <IconGlobe className="h-4 w-4" />
          : kind === 'verification' ? (event.kind === 'verification_completed' && event.payload.verification === 'passed' ? <IconCheck className="h-4 w-4" /> : <IconShield className="h-4 w-4" />)
            : <IconInfo className="h-4 w-4" />;
    const beatTone = kind === 'loop_guard' ? 'text-[var(--cx-amber)]'
      : kind === 'verification' && event.kind === 'verification_completed' && event.payload.verification !== 'passed' ? 'text-[var(--cx-red)]'
        : 'text-[var(--cx-teal-strong)]';
    return (
      <div key={event.seq} data-testid={`story-beat-${event.seq}`} className="flex items-center gap-2.5 px-1 py-1">
        <span className={beatTone}>{beatIcon}</span>
        <p className="text-xs text-[var(--cx-ink-dim)]">{beatText(event)}</p>
        <span className="cx-mono ml-auto shrink-0 text-[0.62rem] text-[var(--cx-ink-faint)]">{formatClock(event.created_at)}</span>
      </div>
    );
  };

  return (
    <div className="space-y-2 p-3">
      <style>{`
        @keyframes foldEnter {
          from { opacity: 0; transform: translateY(-4px) scale(.92); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes foldShimmer {
          0% { background-position: 0% 50%; }
          100% { background-position: 200% 50%; }
        }
        @keyframes foldEventEnter {
          from { opacity: 0; transform: translateY(-10px) scale(.97); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes foldEventGlow {
          0%, 100% { border-color: rgba(144,133,233,.38); box-shadow: 0 0 0 rgba(144,133,233,0); }
          50% { border-color: rgba(144,133,233,.85); box-shadow: 0 0 18px rgba(144,133,233,.18); }
        }
        .fold-event {
          border-color: rgba(144,133,233,.38);
          background: linear-gradient(120deg, rgba(98,80,214,.07), rgba(27,175,122,.04));
          animation: foldEventEnter .55s ease-out, foldEventGlow 2.8s ease-in-out infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .fold-badge, .fold-event { animation: none !important; }
        }
      `}</style>
      {groups.length === 0 ? <p className="px-1 py-8 text-center text-sm text-[var(--cx-ink-faint)]">{emptyHint}</p> : null}
      {groups.map((group) => {
        const tools = group.events.filter((event) => storyKindOf(event) === 'tool').length;
        return (
          <section
            key={group.key}
            data-testid={group.turn === null ? `story-turn-seq-${group.events[0]?.seq ?? 0}` : `story-turn-${group.turn}`}
            className="space-y-2 rounded-xl p-2"
            style={TURN_STYLE}
          >
            <header className="flex items-center gap-2 px-1.5 pt-0.5">
              <span className="cx-mono rounded-md border border-[var(--cx-line-strong)] bg-[#fbfaf6] px-1.5 py-0.5 text-[0.66rem] font-semibold text-[var(--cx-teal-strong)]">
                {group.turn === null ? '平台事件' : `第 ${group.turn} 轮`}
              </span>
              <span className="text-[0.62rem] text-[var(--cx-ink-faint)]">
                {group.events.length} 条{group.turn === null ? '' : ` · ${tools} 次工具调用`}
              </span>
            </header>
            {group.events.map((event) => renderEvent(event))}
          </section>
        );
      })}
    </div>
  );
}
