import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type Node,
  type NodeChange,
  type Edge,
  type ReactFlowInstance,
} from '@xyflow/react';
import { Trash2, Unplug, Activity, LayoutGrid, Maximize } from 'lucide-react';
import '@xyflow/react/dist/style.css';
import { devLogger } from '@/app/utils/devLogger';
import { useVisualLLMConfig } from './hooks/useVisualLLMConfig';
import { nodeTypes, edgeTypes } from './utils/nodeTypes';
import { validateVisualGraph } from './utils/validation';
import { extractNodePositions, extractNodeStates } from './utils/configConverter';
import { ContextMenu, type ContextMenuItem } from './components/ContextMenu';
import { ValidationPanel, ValidationBadge } from './components/ValidationPanel';
import type {
  VisualEdgeData,
  VisualGraphConfig,
  VisualGraphStatus,
  VisualNodeData,
  VisualProviderNodeData,
  VisualModelNodeData,
  VisualRoleNodeData,
} from './types/visual';

interface LLMVisualEditorProps {
  config: VisualGraphConfig | null;
  status?: VisualGraphStatus | null;
  onConfigChange?: (config: VisualGraphConfig) => void;
  onSave?: (config?: VisualGraphConfig) => void;
}

type ContextMenuState = {
  visible: boolean;
  x: number;
  y: number;
  type: 'node' | 'edge';
  data: Node<VisualNodeData> | Edge;
};

type LayoutPoint = { x: number; y: number };

function extractLayoutWithFallback(nodes: Node<VisualNodeData>[]): Record<string, LayoutPoint> {
  const layout = extractNodePositions(nodes);
  if (Object.keys(layout).length > 0) {
    return layout;
  }

  const fallback: Record<string, LayoutPoint> = {};
  nodes.forEach((node) => {
    const rawNode = node as Node<VisualNodeData> & {
      positionAbsolute?: { x: number; y: number };
      positionAbsoluteX?: number;
      positionAbsoluteY?: number;
    };
    const absolute = rawNode.positionAbsolute;
    if (absolute && typeof absolute.x === 'number' && typeof absolute.y === 'number') {
      fallback[node.id] = { x: absolute.x, y: absolute.y };
      return;
    }
    if (
      typeof rawNode.positionAbsoluteX === 'number' &&
      typeof rawNode.positionAbsoluteY === 'number'
    ) {
      fallback[node.id] = { x: rawNode.positionAbsoluteX, y: rawNode.positionAbsoluteY };
    }
  });
  return fallback;
}

export function LLMVisualEditor({ config, status, onConfigChange, onSave }: LLMVisualEditorProps) {
  const editorContainerRef = useRef<HTMLDivElement | null>(null);
  // Validation
  const validation = useMemo(() => {
    if (!config) return { valid: true, issues: [] };
    return validateVisualGraph(config);
  }, [config]);

  const {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onNodesDelete,
    onEdgesDelete,
    onConnect,
    addModel,
    clearRoleAssignment,
    deleteNode,
    deleteEdge,
    setNodes,
    getCurrentConfig,
  } = useVisualLLMConfig({ config, status, onConfigChange });

  const [rfInstance, setRfInstance] = useState<ReactFlowInstance<Node<VisualNodeData>, Edge> | null>(null);
  const [modelDraft, setModelDraft] = useState('');
  const [providerDraft, setProviderDraft] = useState('');
  const [showAddModel, setShowAddModel] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [showValidationPanel, setShowValidationPanel] = useState(true);

  const providers = useMemo(() => Object.entries(config?.providers || {}), [config]);

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node<VisualNodeData>) => {
      event.preventDefault();
      const containerRect = editorContainerRef.current?.getBoundingClientRect();
      const menuX = containerRect ? event.clientX - containerRect.left : event.clientX;
      const menuY = containerRect ? event.clientY - containerRect.top : event.clientY;
      setContextMenu({
        visible: true,
        x: menuX,
        y: menuY,
        type: 'node',
        data: node,
      });
    },
    []
  );

  const onEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      event.preventDefault();
      const containerRect = editorContainerRef.current?.getBoundingClientRect();
      const menuX = containerRect ? event.clientX - containerRect.left : event.clientX;
      const menuY = containerRect ? event.clientY - containerRect.top : event.clientY;
      setContextMenu({
        visible: true,
        x: menuX,
        y: menuY,
        type: 'edge',
        data: edge,
      });
    },
    []
  );

  const onPaneClick = useCallback(() => {
    setContextMenu(null);
  }, []);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const focusNode = useCallback((nodeId: string) => {
    const node = nodes.find(n => n.id === nodeId);
    if (node && rfInstance) {
      rfInstance.setCenter(node.position.x, node.position.y, { zoom: 1.2, duration: 400 });
    }
  }, [nodes, rfInstance]);

  const handleAutoLayout = useCallback(() => {
    const updates: Node<VisualNodeData>[] = [];
    
    // Group nodes by type
    const providers = nodes.filter(n => n.type === 'provider');
    const models = nodes.filter(n => n.type === 'model');
    const roles = nodes.filter(n => n.type === 'role');
    const others = nodes.filter(n => !['provider', 'model', 'role'].includes(n.type || ''));

    providers.forEach((node, index) => {
      updates.push({ ...node, position: { x: 40, y: index * 180 + 40 } });
    });
    models.forEach((node, index) => {
      updates.push({ ...node, position: { x: 340, y: index * 120 + 40 } });
    });

    roles.forEach((node, index) => {
      updates.push({ ...node, position: { x: 700, y: index * 180 + 40 } });
    });
    
    others.forEach((node, index) => {
      updates.push({ ...node, position: { x: 1000, y: index * 180 + 40 } });
    });

    setNodes(updates);
    rfInstance?.fitView({ duration: 800 });
  }, [nodes, rfInstance, setNodes]);

  // Generate menu items based on context
  const getContextMenuItems = useCallback((): { items: ContextMenuItem[]; title?: string } => {
    if (!contextMenu) return { items: [] };

    if (contextMenu.type === 'node') {
      const node = contextMenu.data as Node<VisualNodeData>;
      const items: ContextMenuItem[] = [];
      let title = '';

      if (node.type === 'provider') {
        const data = node.data as VisualProviderNodeData;
        title = `提供商：${data.label}`;
        items.push({
          label: '测试连接',
          icon: Activity,
          action: () => {
             devLogger.debug('Test provider', data.providerId);
          },
        });
        items.push({
          label: '删除 Provider',
          icon: Trash2,
          variant: 'danger',
          action: () => deleteNode(node.id),
        });
      } else if (node.type === 'model') {
         const data = node.data as VisualModelNodeData;
         title = `模型：${data.model}`;
         items.push({
          label: '删除模型',
          icon: Trash2,
          variant: 'danger',
          action: () => deleteNode(node.id),
        });
      } else if (node.type === 'role') {
         const data = node.data as VisualRoleNodeData;
         title = `角色：${data.label}`;
         items.push({
           label: '清除分配',
           icon: Unplug,
           variant: 'warning',
           action: () => clearRoleAssignment(data.roleId),
         });
      }
      return { items, title };
    } else if (contextMenu.type === 'edge') {

      const edge = contextMenu.data as Edge;
      return {
        title: '连接操作',
        items: [
          {
            label: '删除连接',
            icon: Unplug,
            variant: 'danger',
            action: () => deleteEdge(edge.id),
          },
        ],
      };
    }
    return { items: [] };
  }, [clearRoleAssignment, contextMenu, deleteNode, deleteEdge]);

  const contextMenuView = useMemo(() => getContextMenuItems(), [getContextMenuItems]);

  useEffect(() => {
    if (!providerDraft && providers.length > 0) {
      setProviderDraft(providers[0][0]);
    }
  }, [providerDraft, providers]);

  const handleAddModel = () => {
    const modelName = modelDraft.trim();
    if (!modelName || !providerDraft) return;
    addModel(providerDraft, modelName);
    setModelDraft('');
  };

  const isValid = useCallback((connection: Connection | Edge) => {
    return Boolean(connection.source && connection.target);
  }, []);

  const nodeColor = (node: Node<VisualNodeData>) => {
    if (node.type === 'role') return '#22d3ee';
    if (node.type === 'provider') return '#f472b6';
    return '#34d399';
  };

  const handleNodesChange = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes);
  }, [onNodesChange]);

  if (!config) {
    return (
      <div className="rounded-xl border border-white/10 bg-black/30 p-6 text-xs text-text-dim">
        暂无 LLM 配置数据，无法渲染吏部·铨选司。
      </div>
    );
  }

  const handleSave = () => {
    if (!onConfigChange) return;
    const baseConfig = getCurrentConfig() || config;
    if (!baseConfig) return;

    const latestNodes =
      (rfInstance?.getNodes() as Node<VisualNodeData>[] | undefined) || nodes;
    const latestEdges =
      (rfInstance?.getEdges() as Edge<VisualEdgeData>[] | undefined) || edges;
    const layout = extractLayoutWithFallback(latestNodes);
    const states = extractNodeStates(latestNodes, latestEdges);

    const finalConfig = {
      ...baseConfig,
      visual_layout: layout,
      visual_node_states: states,
    };

    onConfigChange(finalConfig);
    onSave?.(finalConfig);
  };

  return (
    <div
      data-testid="llm-visual-editor"
      ref={editorContainerRef}
      className="relative rounded-2xl border border-white/10 bg-black/40 p-4 shadow-[0_0_24px_rgba(34,211,238,0.12)]"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-xs font-semibold text-text-main">LLM 视觉配置编辑器 · 吏部·铨选司</div>
          <div className="text-[10px] text-text-dim">拖拽连线：提供商 → 模型 → 角色</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleAutoLayout}
            className="p-1.5 text-text-dim hover:text-cyan-400 transition-colors"
            title="自动布局"
          >
            <LayoutGrid size={14} />
          </button>
          <button
            type="button"
            onClick={() => rfInstance?.fitView({ duration: 400 })}
            className="p-1.5 text-text-dim hover:text-cyan-400 transition-colors"
            title="适应视图"
          >
            <Maximize size={14} />
          </button>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <button
            type="button"
            onClick={() => setShowAddModel((prev) => !prev)}
            className="px-3 py-1.5 text-[10px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors"
          >
            添加模型
          </button>
          {onSave ? (
            <button
              type="button"
              data-testid="llm-visual-save"
              onClick={handleSave}
              className="px-3 py-1.5 text-[10px] font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors"
            >
              保存配置
            </button>
          ) : null}
          {validation.issues.length > 0 && (
            <button
              type="button"
              onClick={() => setShowValidationPanel(v => !v)}
              className="ml-2"
            >
              <ValidationBadge count={validation.issues.length} />
            </button>
          )}
        </div>
      </div>

      {showAddModel ? (
        <div className="mb-3 grid grid-cols-1 md:grid-cols-[180px_1fr_auto] gap-2 items-center">
          <select
            className="bg-black/40 border border-white/10 text-[10px] text-text-main rounded px-2 py-1.5"
            value={providerDraft}
            onChange={(event) => setProviderDraft(event.target.value)}
          >
            <option value="">选择提供商</option>
            {providers.map(([providerId, provider]) => {
              const label =
                typeof provider === 'object' && provider !== null && 'name' in provider
                  ? String((provider as Record<string, unknown>).name || providerId)
                  : providerId;
              return (
                <option key={providerId} value={providerId}>
                  {label}
                </option>
              );
            })}
          </select>
          <input
            className="bg-black/40 border border-white/10 text-[10px] text-text-main rounded px-2 py-1.5"
            placeholder="模型名称"
            value={modelDraft}
            onChange={(event) => setModelDraft(event.target.value)}
          />
          <button
            type="button"
            onClick={handleAddModel}
            className="px-3 py-1.5 text-[10px] font-semibold bg-fuchsia-500/80 hover:bg-fuchsia-500 text-white rounded"
          >
            添加
          </button>
        </div>
      ) : null}

      <div className="h-[60vh] min-h-[520px] rounded-xl border border-white/10 overflow-hidden">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onNodesDelete={onNodesDelete}
          onEdgesChange={onEdgesChange}
          onEdgesDelete={onEdgesDelete}
          onConnect={onConnect}
          onNodeContextMenu={onNodeContextMenu}
          onEdgeContextMenu={onEdgeContextMenu}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          isValidConnection={isValid}
          className="bg-[radial-gradient(circle_at_top,_rgba(14,116,144,0.18),_transparent_60%)]"
        >
          <MiniMap
            nodeColor={nodeColor}
            maskColor="rgba(15,23,42,0.6)"
            className="bg-black/70"
          />
          <Controls className="bg-black/60" />
          <Background gap={24} size={1} color="rgba(148,163,184,0.35)" />
        </ReactFlow>
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenuView.items}
          title={contextMenuView.title}
          onClose={closeContextMenu}
        />
      )}

      {showValidationPanel && validation.issues.length > 0 && (
        <ValidationPanel
          issues={validation.issues}
          onIssueClick={(issue) => focusNode(issue.nodeId)}
          onClose={() => setShowValidationPanel(false)}
        />
      )}
    </div>
  );
}
