import { useMemo, useState } from 'react';
import {
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
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
  nodesFromManifest,
  type ContextFoldState,
  type ContextNode,
  type ContextNodeKind,
} from './contextOSNodeMapModel';
import type { EngineeringEvent } from '@/services/api.types';
import type { ContextPayload } from './ContextViewerModal';
import type { FinalProviderRequestPayload } from './finalProviderRequestProtocol';
import type { ContextProjectionManifest, ProjectionRunCard } from './contextProjectionManifest';

const KIND_STYLE: Record<ContextNodeKind, { color: string; label: string; icon: typeof GitBranch }> = {
  goal: { color: '#3987e5', label: '目标', icon: FileText },
  file_card: { color: '#3987e5', label: 'FileCard', icon: FileText },
  command_card: { color: '#d95926', label: 'CommandCard', icon: Terminal },
  message: { color: '#d95926', label: '消息', icon: MessageSquare },
  tool_call: { color: '#9085e9', label: '工具调用', icon: Terminal },
  tool_log: { color: '#199e70', label: '工具日志', icon: Layers },
  verification: { color: '#e66767', label: '验证', icon: ShieldCheck },
  run_card: { color: '#0ca30c', label: 'RunCard', icon: GitBranch },
  artifact: { color: '#c98500', label: 'Artifact', icon: Package },
  request: { color: '#d55181', label: '最终请求', icon: FileText },
  failure_cluster: { color: '#e66767', label: '失败聚类', icon: ShieldCheck },
  interface_contract: { color: '#3987e5', label: '接口契约', icon: GitBranch },
  requirement_trace: { color: '#c98500', label: '需求追踪', icon: ShieldCheck },
  tool_pattern: { color: '#199e70', label: '工具经验', icon: Terminal },
  file_diff: { color: '#9085e9', label: '文件变更', icon: FileText },
  evidence_lease: { color: '#1f7a8c', label: '证据租约', icon: Link2 },
  epoch_card: { color: '#d55181', label: 'Epoch记忆', icon: Layers },
};

const FOLD_STYLE: Record<ContextFoldState, { label: string; icon: typeof GitBranch; className: string; bar: string }> = {
  ACTIVE: { label: 'ACTIVE · 当前请求', icon: CheckCircle2, className: 'text-emerald-300', bar: '#199e70' },
  FOLDED: { label: 'FOLDED · 已折叠', icon: ChevronDown, className: 'text-violet-300', bar: '#9085e9' },
  REFERENCE: { label: 'REFERENCE · 证据引用', icon: Link2, className: 'text-cyan-300', bar: '#3987e5' },
  EVICTED: { label: 'EVICTED · 已移出', icon: Archive, className: 'text-slate-400', bar: '#64748b' },
  UNKNOWN: { label: 'UNKNOWN · 未证明', icon: CircleDashed, className: 'text-amber-300', bar: '#c98500' },
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
  manifest?: ContextProjectionManifest | null;
  onEvidenceSelect?: (reference: string, messageIndex?: number) => void;
}

function NodeButton({ node, selected, onSelect }: { node: ContextNode; selected: boolean; onSelect: () => void }) {
  const kind = KIND_STYLE[node.kind];
  const KindIcon = kind.icon;
  const FoldIcon = FOLD_STYLE[node.foldState].icon;
  return (
    <button
      type="button"
      role="listitem"
      onClick={onSelect}
      data-testid={`contextos-node-${node.id}`}
      data-fold-state={node.foldState}
      className={cn(
        'flex w-full items-start gap-2 rounded-md border-l-2 px-2 py-1.5 text-left transition-colors',
        selected ? 'bg-white/[0.05] ring-1 ring-accent-secondary/60' : 'hover:bg-white/[0.03]',
      )}
      style={{ borderLeftColor: kind.color }}
      title={node.foldReason}
    >
      <KindIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: kind.color }} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-[10px] font-medium text-text-main">
            {node.id.startsWith('request-') ? 'REQ' : `#${node.seq}`} · {node.title}
          </span>
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
        {node.foldReasonCode || node.foldState}
      </span>
    </button>
  );
}

export function ContextOSNodeMap({ content, finalRequest, events = [], manifest = null, onEvidenceSelect }: ContextOSNodeMapProps) {
  const [filter, setFilter] = useState<ContextFoldState | 'ALL'>('ALL');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [openCards, setOpenCards] = useState<Record<string, boolean>>({});
  const nodes = useMemo(
    () => (manifest ? nodesFromManifest(manifest) : buildContextNodes(content, finalRequest, events)),
    [manifest, content, finalRequest, events],
  );
  const counts = useMemo(() => foldStateCounts(nodes), [nodes]);
  const cards = useMemo<ProjectionRunCard[]>(() => {
    if (!manifest) {
      return [
        { id: 'all', seq: 0, role: 'all', phase: null, verification: 'unknown', workspace_revision: null, child_ids: nodes.map((node) => node.id) },
      ];
    }
    const memoryCards: ProjectionRunCard[] = manifest.memory_cards?.length
      ? [
          {
            id: 'memory-cards',
            seq: 0,
            role: 'ContextOS',
            phase: 'long-term-memory',
            verification: 'unknown',
            workspace_revision: null,
            child_ids: manifest.memory_cards.map((card) => card.id),
          },
        ]
      : [];
    return [...manifest.run_cards, ...memoryCards];
  }, [manifest, nodes]);
  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const original = manifest?.summary.original_tokens ?? nodes.reduce((sum, node) => sum + (node.originalTokens ?? node.approxTokens ?? 0), 0);
  const projected = manifest?.summary.projected_tokens ?? nodes.reduce((sum, node) => sum + (node.projectedTokens ?? 0), 0);
  const ratio = original > 0 ? projected / original : null;

  return (
    <section
      className="flex min-h-0 flex-col rounded-lg border border-white/[0.07] bg-black/20"
      data-testid="contextos-node-map"
      data-manifest-loaded={manifest ? 'yes' : 'no'}
      data-memory-cards={manifest?.memory_cards?.length ?? 0}
    >
      <header className="border-b border-white/[0.06] px-3 py-2">
        <div className="flex items-center gap-2 text-[11px] font-semibold text-text-main">
          <GitBranch className="h-4 w-4 text-accent-secondary" />
          ContextOS 节点地图
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-text-dim">
          RunCard 父子节点、折叠来源与证据引用。token 为约值；无法证明时保持 UNKNOWN。
        </p>
        <div className="mt-2" data-testid="contextos-compression-bar" title={`保留/投影约 ${projected} / 原始约 ${original} tokens`}>
          <div className="mb-1 flex justify-between text-[9px] text-text-dim">
            <span>上下文投影分布</span>
            <span>≈{projected} / ≈{original} tok{ratio !== null ? ` · ${(ratio * 100).toFixed(1)}%` : ''}</span>
          </div>
          <div className="flex h-2 overflow-hidden rounded-full bg-white/[0.05]" role="img" aria-label={`上下文投影约 ${projected}，原始约 ${original} tokens`}>
            {(Object.keys(FOLD_STYLE) as ContextFoldState[]).map((state) => {
              const value = nodes.filter((node) => node.foldState === state).reduce((sum, node) => sum + (node.projectedTokens ?? 0), 0);
              const width = projected > 0 ? (value / projected) * 100 : 0;
              return width > 0 ? <span key={state} style={{ width: `${width}%`, backgroundColor: FOLD_STYLE[state].bar }} title={`${state}: ≈${value} tok`} /> : null;
            })}
          </div>
        </div>
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
              className={cn('rounded px-1.5 py-0.5 text-[9px]', filter === item.key ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-text-dim hover:bg-white/5')}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>

      <div className="max-h-[48vh] min-h-0 flex-1 space-y-1 overflow-y-auto p-2" role="list" aria-label="ContextOS 节点">
        {cards.map((card) => {
          const children = nodes.filter((node) => node.parentId === card.id || (!manifest && card.id === 'all'));
          const visible = filter === 'ALL' ? children : children.filter((node) => node.foldState === filter);
          if (!visible.length) return null;
          const open = openCards[card.id] ?? true;
          return (
            <details key={card.id} open={open} className="rounded-md border border-white/[0.05] bg-white/[0.01]">
              <summary
                className="flex cursor-pointer items-center gap-2 px-2 py-1.5 text-[10px] text-text-muted hover:bg-white/[0.03]"
                onClick={(event) => {
                  event.preventDefault();
                  setOpenCards((previous) => ({ ...previous, [card.id]: !open }));
                }}
                data-testid={`contextos-runcard-${card.id}`}
              >
                {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                <span className="font-semibold text-text-main">{card.id === 'memory-cards' ? '长期记忆卡' : `RunCard #${card.seq}`}</span>
                <span>{card.role}</span>
                {card.phase && <span>phase={card.phase}</span>}
                {card.workspace_revision !== null && <span>rev={card.workspace_revision}</span>}
                <span className="ml-auto font-mono text-[9px] text-text-dim">{children.length} 节点</span>
              </summary>
              <div className="space-y-1 p-1.5">
                {visible.map((node) => (
                  <NodeButton key={node.id} node={node} selected={selectedId === node.id} onSelect={() => setSelectedId(node.id)} />
                ))}
              </div>
            </details>
          );
        })}
      </div>

      {selected && (
        <aside className="border-t border-white/[0.06] p-3" data-testid="contextos-node-detail">
          <div className="flex items-center gap-2 text-[10px] font-semibold text-text-main">
            <span style={{ color: KIND_STYLE[selected.kind].color }}>{KIND_STYLE[selected.kind].label}</span>
            <span>{selected.title}</span>
          </div>
          <p className="mt-1 text-[10px] leading-relaxed text-text-muted">{selected.summary || '—'}</p>
          <p className="mt-1 text-[9px] leading-relaxed text-text-dim">{selected.foldReason}</p>
          {selected.projectionRef && <p className="mt-1 font-mono text-[9px] text-text-dim">projection: {selected.projectionRef}</p>}
          {selected.pinReason && <p className="mt-1 text-[9px] text-cyan-300">pin: {selected.pinReason}</p>}
          {selected.supersededBy && <p className="mt-1 text-[9px] text-violet-300">superseded by: {selected.supersededBy}</p>}
          {selected.evidenceRef && (
            <button
              type="button"
              onClick={() => onEvidenceSelect?.(selected.evidenceRef!, selected.requestMessageIndex ?? undefined)}
              className="mt-2 rounded bg-accent-secondary/15 px-2 py-1 text-[10px] text-accent-secondary hover:bg-accent-secondary/25"
              data-testid="contextos-node-evidence-link"
            >
              在最终请求中查找引用：{selected.evidenceRef}
            </button>
          )}
        </aside>
      )}
      {!manifest && !events.length && (
        <footer className="border-t border-white/[0.06] px-3 py-1.5 text-[9px] text-text-dim">
          当前仅加载请求快照；未提供完整 engineering journal，早期折叠历史可能为 UNKNOWN。
        </footer>
      )}
    </section>
  );
}
