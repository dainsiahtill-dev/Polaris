/** Left rail for the live run view: ContextOS structure, memory and metrics.
 *
 * Default is the 2D layered graph. The 3D view is an opt-in exploration mode over
 * the same nodes; 2D stays the readable, keyboard-reachable form.
 */
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Archive,
  Box,
  Brain,
  ChevronRight,
  FileText,
  FlaskConical,
  Layers,
  Map as MapIcon,
  Send,
  ShieldCheck,
  Sparkles,
  Terminal,
} from 'lucide-react';
import { cn } from '@/app/components/ui/utils';
import type { EngineeringEvent } from '@/services/api.types';
import {
  LIVE_FOLD_LABELS,
  LIVE_KIND_LABELS,
  LIVE_LAYER_LABELS,
  LIVE_SUMMARY_KIND_LABELS,
  LIVE_SUMMARY_KIND_ORDER,
  buildLiveContextGraph,
  layoutLiveContextNodes,
  liveContextMetrics,
  liveMemoryCards,
  liveProjectedCards,
  type LiveContextNode,
  type LiveFoldState,
  type LiveNodeKind,
} from './contextOSNavigatorModel';
import { COMPRESSION_DROP_UNIT_LABELS, type ContextCompressionFact } from './contextOSCompression';
import { ContextOSNavigator3D } from './ContextOSNavigator3D';

const KIND_ICON: Record<LiveNodeKind, typeof FileText> = {
  working_state: Box,
  agent: Brain,
  epoch: Layers,
  run_card: MapIcon,
  request: Send,
  model_call: Sparkles,
  file_card: FileText,
  command_card: Terminal,
  verification: ShieldCheck,
  fold: Archive,
  distill: FlaskConical,
};

/** Fold state is the status channel; kind identity is carried by icon + label. */
const FOLD_COLOR: Record<LiveFoldState, string> = {
  ACTIVE: '#0ca30c',
  FOLDED: '#ec835a',
  REFERENCE: '#898781',
  EVICTED: '#383835',
  UNKNOWN: '#fab219',
};

const FOLD_ORDER: LiveFoldState[] = ['ACTIVE', 'FOLDED', 'REFERENCE', 'EVICTED', 'UNKNOWN'];

export interface LiveNodeData extends Record<string, unknown> {
  label: string;
  sublabel: string;
  meta: string;
  foldLabel: string;
  summaryKindLabel: string;
  color: string;
  icon: LiveNodeKind;
}

type LiveFlowNode = Node<LiveNodeData, 'live'>;

function LiveNode({ data, selected }: NodeProps<LiveFlowNode>): ReactElement {
  const Icon = KIND_ICON[data.icon];
  return (
    <div
      className="flex min-w-0 items-start gap-2 rounded-md border border-white/10 bg-[#141413] px-2 py-1.5 shadow-sm"
      style={{
        borderLeftWidth: 3,
        borderLeftColor: data.color,
        boxShadow: selected ? `0 0 0 1px ${data.color}` : undefined,
      }}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: data.color }} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[10px] font-medium text-[#f0efec]" title={data.label}>{data.label}</span>
        <span className="block truncate text-[9px] text-[#c3c2b7]" title={data.sublabel}>{data.sublabel}</span>
        <span className="mt-0.5 block truncate text-[8px] text-[#898781]">
          {data.meta}
          {data.summaryKindLabel ? <span className="text-[#c3c2b7]"> · {data.summaryKindLabel}</span> : null}
        </span>
      </span>
      <span className="mt-0.5 shrink-0 text-[8px]" style={{ color: data.color }}>
        {data.foldLabel.split(' · ')[0]}
      </span>
    </div>
  );
}

const nodeTypes = { live: LiveNode };

type Tab = 'structure' | 'memory' | 'metrics';
type ViewMode = '2d' | '3d';

export interface ContextOSNavigatorProps {
  events: EngineeringEvent[];
  selectedSeq?: number | null;
  onOpenContext?: (ref: string, role: string) => void;
}

function metricsRow(label: string, value: string | number): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[10px] text-[#c3c2b7]">{label}</span>
      <span className="cx-mono text-[11px] text-[#f0efec]">{value}</span>
    </div>
  );
}

/**
 * Quote the host's own compression marker. The text is never rewritten: the
 * numbers below are parsed from it, so a missing budget stays visible as a
 * missing budget instead of being filled with a plausible-looking default.
 */
function CompressionNote({ compression, used, budgetSource, utilized }: {
  compression: ContextCompressionFact;
  used: number | null;
  budgetSource: 'compression_note' | 'model_window' | null;
  utilized: number | null;
}): ReactElement {
  return (
    <div
      className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2 py-1.5"
      data-testid="contextos-nav-compression"
    >
      <p className="text-[9px] font-semibold text-[#f0efec]">宿主压缩说明</p>
      <p className="mt-0.5 text-[9px] leading-relaxed text-[#c3c2b7]">{compression.text}</p>
      <p className="mt-1 text-[8px] text-[#898781]">
        丢弃 {compression.droppedTurns} 条较早的{COMPRESSION_DROP_UNIT_LABELS[compression.dropUnit]}
        {compression.budgetTokens !== null ? ` · 预算约 ${compression.budgetTokens.toLocaleString()} tokens` : ''}
        {used !== null ? ` · 实际占用 ${used.toLocaleString()} tokens` : ''}
        {utilized !== null ? ` · 占用率 ${(utilized * 100).toFixed(2)}%` : ''}
        {budgetSource === 'model_window' ? ' · 预算取模型窗口' : ''}
      </p>
    </div>
  );
}

export function ContextOSNavigator({ events, selectedSeq = null, onOpenContext }: ContextOSNavigatorProps): ReactElement {
  const [tab, setTab] = useState<Tab>('structure');
  const [view, setView] = useState<ViewMode>('2d');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const graph = useMemo(() => buildLiveContextGraph(events, 90), [events]);
  const metrics = useMemo(() => liveContextMetrics(events), [events]);
  const memory = useMemo(() => liveMemoryCards(events), [events]);
  const projectedCards = useMemo(() => liveProjectedCards(events), [events]);
  const positions = useMemo(() => layoutLiveContextNodes(graph.nodes), [graph.nodes]);

  const flowNodes = useMemo<LiveFlowNode[]>(() => graph.nodes.map((node) => {
    const position = positions.get(node.id) ?? { x: 0, y: 0 };
    return {
      id: node.id,
      type: 'live',
      position,
      data: {
        label: node.title,
        sublabel: node.summary || '—',
        meta: `${LIVE_KIND_LABELS[node.kind]} · ${node.role}${node.tokens !== null ? ` · ≈${node.tokens} tok` : ''}`,
        foldLabel: LIVE_FOLD_LABELS[node.foldState],
        summaryKindLabel: node.summaryKind ? LIVE_SUMMARY_KIND_LABELS[node.summaryKind] : '',
        color: FOLD_COLOR[node.foldState],
        icon: node.kind,
      },
    };
  }), [graph.nodes, positions]);

  const flowEdges = useMemo<Edge[]>(() => graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label,
    animated: edge.animated,
    style: { stroke: '#383835', strokeWidth: 1.2 },
    labelStyle: { fill: '#898781', fontSize: 9 },
    labelBgStyle: { fill: '#141413' },
  })), [graph.edges]);

  const [nodes, setNodes, onNodesChange] = useNodesState<LiveFlowNode>(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(flowEdges);

  // Keep the flow in sync with the journal without remounting the component.
  useEffect(() => { setNodes(flowNodes); }, [flowNodes, setNodes]);
  useEffect(() => { setEdges(flowEdges); }, [flowEdges, setEdges]);

  const onNodeClick: NodeMouseHandler<LiveFlowNode> = useCallback((_event, node) => {
    setSelectedId(node.id);
  }, []);

  const selected = graph.nodes.find((node) => node.id === selectedId)
    ?? graph.nodes.find((node) => node.seq === selectedSeq)
    ?? null;
  // Fold state and summary provenance live in the projected request, not in the
  // raw tool observation, so the detail looks the card up by its content_ref.
  const projectedCard = selected?.evidenceRef
    ? projectedCards.find((card) => card.nodeId === selected.evidenceRef) ?? null
    : null;
  const detailSummaryKind = projectedCard?.summaryKind ?? selected?.summaryKind ?? null;
  const detailFoldReason = projectedCard?.foldReason || selected?.foldReason || '';
  const layerOptions = [...new Set(graph.nodes.map((node) => node.layer))].sort((a, b) => a - b);

  return (
    <section
      className="cx-card flex max-h-[72vh] min-h-[20rem] flex-col overflow-hidden"
      data-testid="contextos-navigator"
      data-view={view}
      aria-label="ContextOS 内部结构"
    >
      <div className="cx-card-head">
        <h2 className="cx-card-title"><Layers className="h-4.5 w-4.5 text-[var(--cx-teal)]" />ContextOS 结构</h2>
        <span className="cx-pill cx-pill--idle">{graph.nodes.length} 节点</span>
      </div>
      <div className="flex items-center gap-1 border-b border-[var(--cx-line)] px-2 py-1.5">
        {([['structure', '结构'], ['memory', '记忆'], ['metrics', '指标']] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-pressed={tab === key}
            data-testid={`contextos-nav-tab-${key}`}
            className={cn('rounded px-2 py-1 text-[10px]', tab === key ? 'bg-white/[0.07] text-[#f0efec]' : 'text-[#898781] hover:bg-white/[0.03]')}
          >
            {label}
          </button>
        ))}
        {tab === 'structure' ? (
          <span className="ml-auto flex items-center gap-1" aria-label="视图模式">
            {(['2d', '3d'] as ViewMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => setView(mode)}
                aria-pressed={view === mode}
                data-testid={`contextos-nav-view-${mode}`}
                className={cn('rounded px-1.5 py-0.5 text-[9px] uppercase', view === mode ? 'bg-accent-secondary/15 text-accent-secondary' : 'text-[#898781] hover:bg-white/5')}
              >
                {mode}
              </button>
            ))}
          </span>
        ) : null}
      </div>

      {tab === 'structure' ? (
        <>
          <div className="flex flex-wrap items-center gap-1 border-b border-[var(--cx-line)] px-2 py-1.5" aria-label="折叠状态图例">
            {FOLD_ORDER.map((state) => (
              <span key={state} className="flex items-center gap-1 rounded bg-white/[0.03] px-1.5 py-0.5 text-[8px] text-[#c3c2b7]">
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: FOLD_COLOR[state] }} />
                {LIVE_FOLD_LABELS[state]}
                <span className="cx-mono text-[#898781]">{metrics.foldCounts[state]}</span>
              </span>
            ))}
            {metrics.compression ? (
              <span
                className="ml-auto flex items-center gap-1 rounded bg-white/[0.03] px-1.5 py-0.5 text-[8px] text-[#c3c2b7]"
                data-testid="contextos-nav-compression-chip"
                title={metrics.compression.text}
              >
                丢弃 {metrics.compression.droppedTurns} 条{COMPRESSION_DROP_UNIT_LABELS[metrics.compression.dropUnit]}
              </span>
            ) : null}
          </div>
          <div className="relative min-h-0 flex-1">
            {view === '2d' ? (
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                onNodeClick={onNodeClick}
                onPaneClick={() => setSelectedId(null)}
                nodeTypes={nodeTypes}
                fitView
                fitViewOptions={{ padding: 0.25 }}
                minZoom={0.15}
                maxZoom={2}
                proOptions={{ hideAttribution: true }}
                data-testid="contextos-nav-flow"
              >
                <Background gap={18} size={1} color="rgba(148, 163, 184, 0.12)" className="bg-[#0d0d0d]" />
                <Controls className="!border-[#2c2c2a] !bg-[#141413] [&>button]:!border-[#2c2c2a] [&>button]:!bg-[#1a1a19] [&>button]:!text-[#c3c2b7] [&>button:hover]:!bg-[#2c2c2a]" showInteractive={false} />
              </ReactFlow>
            ) : (
              <ContextOSNavigator3D
                nodes={graph.nodes}
                edges={graph.edges}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            )}
          </div>
          <div className="border-t border-[var(--cx-line)] px-2 py-1 text-[8px] text-[#898781]">
            {graph.truncated ? `仅投影最近 ${graph.nodes.length} / ${graph.total} 个相关事件` : `全部 ${graph.total} 个相关事件`} · 层级 {layerOptions.map((layer) => LIVE_LAYER_LABELS[layer]).join(' → ') || '—'}
          </div>
        </>
      ) : null}

      {tab === 'memory' ? (
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2 cx-scroll" data-testid="contextos-nav-memory">
          {memory.length === 0 ? (
            <p className="px-1 py-6 text-center text-[10px] text-[#898781]">尚无长期记忆卡或智能体决策</p>
          ) : memory.map((card) => (
            <article key={card.id} className="rounded-md border border-white/[0.06] bg-white/[0.02] px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[10px] font-semibold text-[#f0efec]">{card.title}</span>
                <span className={cn('rounded px-1 py-0.5 text-[8px]', card.status === 'VALID' ? 'bg-emerald-400/15 text-emerald-300' : 'bg-amber-400/15 text-amber-300')}>
                  {card.status}
                </span>
              </div>
              <p className="mt-0.5 truncate text-[9px] text-[#c3c2b7]" title={card.summary}>{card.summary}</p>
              {card.evidenceRefs.length ? (
                <p className="mt-0.5 truncate font-mono text-[8px] text-[#898781]">{card.evidenceRefs.join(' · ')}</p>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}

      {tab === 'metrics' ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2 cx-scroll" data-testid="contextos-nav-metrics">
          {metrics.compression ? (
            <CompressionNote
              compression={metrics.compression}
              used={metrics.workingSetUsedTokens}
              budgetSource={metrics.workingSetBudgetSource}
              utilized={metrics.workingSetUtilization}
            />
          ) : null}
          <p className="mb-1 mt-3 text-[10px] font-semibold text-[#f0efec]">工作集与压缩</p>
          {metricsRow(
            '工作集预算',
            metrics.workingSetBudgetTokens === null
              ? '—'
              : `${metrics.workingSetBudgetTokens.toLocaleString()} tok${metrics.workingSetBudgetSource === 'model_window' ? '（模型窗口）' : ''}`,
          )}
          {metricsRow('实际占用', metrics.workingSetUsedTokens === null ? '—' : `${metrics.workingSetUsedTokens.toLocaleString()} tok`)}
          {metricsRow('占用率', metrics.workingSetUtilization === null ? '—' : `${(metrics.workingSetUtilization * 100).toFixed(2)}%`)}
          {metricsRow(
            '丢弃轮次',
            metrics.compression
              ? `${metrics.compression.droppedTurns} 条${COMPRESSION_DROP_UNIT_LABELS[metrics.compression.dropUnit]}`
              : '—',
          )}
          <p className="mb-1 mt-3 text-[10px] font-semibold text-[#f0efec]">上下文成本</p>
          {metricsRow('最近请求约 tokens', metrics.lastTokenEstimate ?? '—')}
          {metricsRow('峰值请求约 tokens', metrics.peakTokenEstimate ?? '—')}
          {metricsRow('窗口 tokens', metrics.contextWindowTokens ?? '—')}
          {metricsRow('窗口利用率', metrics.contextUtilization === null ? '—' : `${(metrics.contextUtilization * 100).toFixed(2)}%`)}
          <p className="mb-1 mt-3 text-[10px] font-semibold text-[#f0efec]">结构计数</p>
          {metricsRow('节点 / 边', `${metrics.nodeCount} / ${metrics.edgeCount}`)}
          {metricsRow('请求 / 模型调用', `${metrics.requestCount} / ${metrics.modelCallCount}`)}
          {metricsRow('工具观察 / 折叠', `${metrics.toolCallCount} / ${metrics.foldEventCount}`)}
          {metricsRow('蒸馏节点 / 智能体', `${metrics.distillCount} / ${metrics.agentDecisionCount}`)}
          {metricsRow('验证 / Epoch', `${metrics.verificationCount} / ${metrics.epochCount}`)}
          {metricsRow('Resident 决策', metrics.agentDecisionCount)}
          <p className="mb-1 mt-3 text-[10px] font-semibold text-[#f0efec]">折叠状态</p>
          {FOLD_ORDER.map((state) => metricsRow(LIVE_FOLD_LABELS[state], metrics.foldCounts[state]))}
          <p className="mb-1 mt-3 text-[10px] font-semibold text-[#f0efec]">最近卡片投影与摘要来源</p>
          {metricsRow('折叠 / 未折叠卡片', `${metrics.cardFoldCounts.folded} / ${metrics.cardFoldCounts.active}`)}
          {LIVE_SUMMARY_KIND_ORDER.map((kind) => metricsRow(
            `${LIVE_SUMMARY_KIND_LABELS[kind]}（${kind}）`,
            metrics.summaryKindCounts[kind],
          ))}
          {metrics.lastSnapshotRef ? (
            <button
              type="button"
              onClick={() => onOpenContext?.(metrics.lastSnapshotRef as string, 'director')}
              className="mt-3 flex w-full items-center gap-1.5 rounded bg-accent-secondary/15 px-2 py-1 text-left text-[10px] text-accent-secondary hover:bg-accent-secondary/25"
              data-testid="contextos-nav-open-final-request"
            >
              <ChevronRight className="h-3 w-3 shrink-0" />
              <span className="truncate font-mono">打开最终请求 {metrics.lastSnapshotRef.slice(0, 10)}…</span>
            </button>
          ) : null}
        </div>
      ) : null}

      {tab === 'structure' && selected ? (
        <aside className="border-t border-[var(--cx-line)] px-3 py-2" data-testid="contextos-nav-detail">
          <div className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: FOLD_COLOR[selected.foldState] }} />
            <span className="truncate text-[10px] font-semibold text-[#f0efec]">{selected.title}</span>
          </div>
          <p className="mt-1 text-[9px] leading-relaxed text-[#c3c2b7]">{selected.summary || '—'}</p>
          <p className="mt-1 text-[8px] text-[#898781]">
            {LIVE_KIND_LABELS[selected.kind]} · {selected.role} · {LIVE_FOLD_LABELS[selected.foldState]}
          </p>
          {detailSummaryKind ? (
            <p className="mt-0.5 text-[8px] text-[#898781]" data-testid="contextos-nav-summary-kind">
              摘要来源 · {LIVE_SUMMARY_KIND_LABELS[detailSummaryKind]}（{detailSummaryKind}）
            </p>
          ) : null}
          <p className="mt-0.5 text-[8px] text-[#898781]">{detailFoldReason}</p>
          {metrics.compression ? (
            <p className="mt-1 text-[8px] leading-relaxed text-[#898781]" data-testid="contextos-nav-dropped-note">
              丢弃说明 · {metrics.compression.text}
            </p>
          ) : null}
          {selected.evidenceRef ? (
            <button
              type="button"
              onClick={() => onOpenContext?.(selected.evidenceRef as string, selected.role)}
              className="mt-1.5 rounded bg-accent-secondary/15 px-2 py-1 text-[9px] text-accent-secondary hover:bg-accent-secondary/25"
              data-testid="contextos-nav-evidence-link"
            >
              在最终请求中查找引用
            </button>
          ) : null}
        </aside>
      ) : null}
    </section>
  );
}
