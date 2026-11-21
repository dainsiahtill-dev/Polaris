import { useMemo, useState } from 'react';
import {
  Archive,
  CheckCircle2,
  CircleDashed,
  ChevronDown,
  FileText,
  GitBranch,
  Layers,
  Link2,
  MessageSquare,
  Package,
  ShieldCheck,
  Terminal,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import {
  buildContextNodes,
  foldStateCounts,
  type ContextFoldState,
  type ContextNode,
  type ContextNodeKind,
} from './contextOSNodeMapModel';
import type { EngineeringEvent } from '@/services/api.types';
import type { ContextPayload } from './ContextViewerModal';
import type { FinalProviderRequestPayload } from './finalProviderRequestProtocol';

const KIND_STYLE: Record<ContextNodeKind, { color: string; label: string; icon: typeof GitBranch }> = {
  goal: { color: '#3987e5', label: '目标', icon: FileText },
  message: { color: '#d95926', label: '消息', icon: MessageSquare },
  tool_call: { color: '#9085e9', label: '工具调用', icon: Terminal },
  tool_log: { color: '#199e70', label: '工具日志', icon: Layers },
  verification: { color: '#e66767', label: '验证', icon: ShieldCheck },
  run_card: { color: '#0ca30c', label: 'RunCard', icon: GitBranch },
  artifact: { color: '#c98500', label: 'Artifact', icon: Package },
  request: { color: '#d55181', label: '最终请求', icon: FileText },
};

const FOLD_STYLE: Record<ContextFoldState, { label: string; icon: typeof GitBranch; className: string }> = {
  ACTIVE: { label: 'ACTIVE · 当前请求', icon: CheckCircle2, className: 'text-emerald-300' },
  FOLDED: { label: 'FOLDED · 已折叠', icon: ChevronDown, className: 'text-violet-300' },
  REFERENCE: { label: 'REFERENCE · 证据引用', icon: Link2, className: 'text-cyan-300' },
  EVICTED: { label: 'EVICTED · 已移出', icon: Archive, className: 'text-slate-400' },
  UNKNOWN: { label: 'UNKNOWN · 未证明', icon: CircleDashed, className: 'text-amber-300' },
};

const FILTERS: Array<{ key: ContextFoldState | 'ALL'; label: string }> = [
  { key: 'ALL', label: '全部' },
  { key: 'ACTIVE', label: '活跃' },
  { key: 'FOLDED', label: '已折叠' },
  { key: 'REFERENCE', label: '证据引用' },
  { key: 'EVICTED', label: '已移出' },
  { key: 'UNKNOWN', label: '未知' },
];

export interface ContextOSNodeMapProps {
  content: ContextPayload | null;
  finalRequest: FinalProviderRequestPayload | null;
  events?: EngineeringEvent[];
  onEvidenceSelect?: (reference: string) => void;
}

export function ContextOSNodeMap({ content, finalRequest, events = [], onEvidenceSelect }: ContextOSNodeMapProps) {
  const [filter, setFilter] = useState<ContextFoldState | 'ALL'>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const nodes = useMemo(() => buildContextNodes(content, finalRequest, events), [content, finalRequest, events]);
  const counts = useMemo(() => foldStateCounts(nodes), [nodes]);
  const visible = filter === 'ALL' ? nodes : nodes.filter((node) => node.foldState === filter);
  const selected = nodes.find((node) => node.id === selectedId) ?? null;

  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-white/[0.07] bg-black/20" data-testid="contextos-node-map">
      <header className="border-b border-white/[0.06] px-3 py-2">
        <div className="flex items-center gap-2 text-[11px] font-semibold text-text-main">
          <GitBranch className="h-4 w-4 text-accent-secondary" />
          ContextOS 节点地图
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-text-dim">
          逐轮 RunCard、工具、日志与折叠状态。比例和 token 均为约值；无法证明时保持 UNKNOWN。
        </p>
        <div className="mt-2 flex flex-wrap gap-1" aria-label="节点状态图例">
          {(Object.keys(FOLD_STYLE) as ContextFoldState[]).map((state) => {
            const Icon = FOLD_STYLE[state].icon;
            return (
              <span key={state} className={cn('flex items-center gap-1 rounded bg-white/[0.03] px-1.5 py-0.5 text-[9px]', FOLD_STYLE[state].className)}>
                <Icon className="h-3 w-3" />
                {FOLD_STYLE[state].label}
                <span className="font-mono text-text-dim">{counts[state]}</span>
              </span>
            );
          })}
        </div>
        <div className="mt-2 flex flex-wrap gap-1" aria-label="节点筛选">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setFilter(item.key)}
              aria-pressed={filter === item.key}
              data-testid={`contextos-node-filter-${item.key}`}
              className={cn(
                'rounded px-1.5 py-0.5 text-[9px]',
                filter === item.key ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-dim hover:bg-white/5',
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>

      <div className="max-h-[46vh] min-h-0 flex-1 space-y-1 overflow-y-auto p-2" role="list" aria-label="ContextOS 节点">
        {visible.map((node) => {
          const kind = KIND_STYLE[node.kind];
          const KindIcon = kind.icon;
          const FoldIcon = FOLD_STYLE[node.foldState].icon;
          const selectedClass = selectedId === node.id ? 'ring-1 ring-accent-secondary/60 bg-white/[0.05]' : 'hover:bg-white/[0.03]';
          return (
            <button
              key={node.id}
              type="button"
              role="listitem"
              onClick={() => setSelectedId(node.id)}
              data-testid={`contextos-node-${node.id}`}
              data-fold-state={node.foldState}
              className={cn('flex w-full items-start gap-2 rounded-md border-l-2 px-2 py-1.5 text-left transition-colors', selectedClass)}
              style={{ borderLeftColor: kind.color }}
              title={node.foldReason}
            >
              <KindIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: kind.color }} />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-[10px] font-medium text-text-main">{node.id.startsWith('request-') ? 'REQ' : `#${node.seq}`} · {node.title}</span>
                  <span className="shrink-0 rounded bg-white/[0.04] px-1 py-0.5 text-[8px] text-text-dim">{kind.label}</span>
                </span>
                <span className="mt-0.5 block truncate text-[9px] text-text-muted" title={node.summary}>{node.summary || '—'}</span>
                <span className="mt-0.5 flex flex-wrap items-center gap-2 text-[8px] text-text-dim">
                  <span>{node.role}</span>
                  {node.phase && <span>phase={node.phase}</span>}
                  {node.approxTokens !== null && <span>≈{node.approxTokens} tok</span>}
                </span>
              </span>
              <span className={cn('mt-0.5 flex shrink-0 items-center gap-1 text-[8px]', FOLD_STYLE[node.foldState].className)}>
                <FoldIcon className="h-3 w-3" />
                {node.foldState}
              </span>
            </button>
          );
        })}
        {!visible.length && <p className="px-2 py-6 text-center text-[10px] text-text-dim">此筛选下没有节点。</p>}
      </div>

      {selected && (
        <aside className="border-t border-white/[0.06] p-3" data-testid="contextos-node-detail">
          <div className="flex items-center gap-2 text-[10px] font-semibold text-text-main">
            <span style={{ color: KIND_STYLE[selected.kind].color }}>{KIND_STYLE[selected.kind].label}</span>
            <span>{selected.title}</span>
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">{selected.summary || '—'}</p>
          <p className="mt-1 text-[9px] leading-relaxed text-text-dim">{selected.foldReason}</p>
          {selected.evidenceRef && (
            <button
              type="button"
              onClick={() => onEvidenceSelect?.(selected.evidenceRef!)}
              className="mt-2 rounded bg-accent-secondary/15 px-2 py-1 text-[10px] text-accent-secondary hover:bg-accent-secondary/25"
              data-testid="contextos-node-evidence-link"
            >
              在最终请求中查找引用：{selected.evidenceRef}
            </button>
          )}
        </aside>
      )}
      {!events.length && (
        <footer className="border-t border-white/[0.06] px-3 py-1.5 text-[9px] text-text-dim">
          当前仅加载请求快照；未提供完整 engineering journal，早期折叠历史可能为 UNKNOWN。
        </footer>
      )}
    </section>
  );
}
