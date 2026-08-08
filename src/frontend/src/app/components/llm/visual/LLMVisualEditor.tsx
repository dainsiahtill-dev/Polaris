import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
} from "@xyflow/react";
import {
  Activity,
  ChevronDown,
  ChevronUp,
  LayoutGrid,
  Maximize,
  SlidersHorizontal,
  Trash2,
  Unplug,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import { devLogger } from "@/app/utils/devLogger";
import { useVisualLLMConfig } from "./hooks/useVisualLLMConfig";
import { nodeTypes, edgeTypes } from "./utils/nodeTypes";
import { validateVisualGraph, isValidVisualConnection } from "./utils/validation";
import {
  extractNodePositions,
  extractNodeStates,
  getRoleBindings,
  updateProviderConcurrency,
  updateRoleBindingConcurrency,
  updateRoleConcurrency,
} from "./utils/configConverter";
import { getLlmRoleDefinition, isKnownLlmRoleId } from "../roleDefinitions";
import { ContextMenu, type ContextMenuItem } from "./components/ContextMenu";
import { ValidationPanel, ValidationBadge } from "./components/ValidationPanel";
import type {
  VisualEdgeData,
  VisualGraphConfig,
  VisualGraphStatus,
  VisualNodeData,
  VisualProviderNodeData,
  VisualModelNodeData,
  VisualRoleNodeData,
  VisualRoleId,
} from "./types/visual";

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
  type: "node" | "edge";
  data: Node<VisualNodeData> | Edge;
};

type LayoutPoint = { x: number; y: number };


const isVisualRoleId = (value: string): value is VisualRoleId =>
  isKnownLlmRoleId(value);

const readPositiveInt = (value: string): number | undefined => {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
};

function extractLayoutWithFallback(
  nodes: Node<VisualNodeData>[],
): Record<string, LayoutPoint> {
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
    if (
      absolute &&
      typeof absolute.x === "number" &&
      typeof absolute.y === "number"
    ) {
      fallback[node.id] = { x: absolute.x, y: absolute.y };
      return;
    }
    if (
      typeof rawNode.positionAbsoluteX === "number" &&
      typeof rawNode.positionAbsoluteY === "number"
    ) {
      fallback[node.id] = {
        x: rawNode.positionAbsoluteX,
        y: rawNode.positionAbsoluteY,
      };
    }
  });
  return fallback;
}

export function LLMVisualEditor({
  config,
  status,
  onConfigChange,
  onSave,
}: LLMVisualEditorProps) {
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

  const [rfInstance, setRfInstance] = useState<ReactFlowInstance<
    Node<VisualNodeData>,
    Edge
  > | null>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [providerDraft, setProviderDraft] = useState("");
  const [showAddModel, setShowAddModel] = useState(false);
  const [showConcurrencyPanel, setShowConcurrencyPanel] = useState(false);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [showValidationPanel, setShowValidationPanel] = useState(true);

  const providers = useMemo(
    () => Object.entries(config?.providers || {}),
    [config],
  );
  const roleConcurrencyRows = useMemo(() => {
    return Object.entries(config?.roles || {})
      .filter(([roleId]) => isVisualRoleId(roleId))
      .map(([roleId, roleCfg]) => ({
        roleId: roleId as VisualRoleId,
        roleCfg,
        bindings: getRoleBindings(roleCfg),
      }))
      .filter(
        (row) =>
          row.bindings.length > 0 ||
          row.roleCfg.provider_id ||
          row.roleCfg.model,
      );
  }, [config]);

  const bindingRows = useMemo(
    () =>
      roleConcurrencyRows.flatMap((row) =>
        row.bindings.map((binding, index) => ({
          roleId: row.roleId,
          binding,
          index,
        })),
      ),
    [roleConcurrencyRows],
  );

  const commitConfigUpdate = useCallback(
    (updater: (current: VisualGraphConfig) => VisualGraphConfig) => {
      const current = getCurrentConfig() || config;
      if (!current || !onConfigChange) return;
      const next = updater(current);
      onConfigChange(next);
    },
    [config, getCurrentConfig, onConfigChange],
  );

  // --- Standard ReactFlow context-menu handlers (simplified, robust) ---
  // Previously used a complex capture-phase handler + DOM traversal + proximity
  // detection. That over-engineering caused the handlers to interfere with each
  // other, breaking right-click on edges. Now using ONLY ReactFlow's native
  // onEdgeContextMenu / onNodeContextMenu / onPaneContextMenu — the standard,
  // well-tested approach. CustomEdge uses interactionWidth={40} for a wide hit
  // area so ReactFlow's edge detection is reliable.

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node<VisualNodeData>) => {
      console.log("[LLMVisualEditor] onNodeContextMenu fired:", node.id, node.type);
      event.preventDefault();
      const rect = editorContainerRef.current?.getBoundingClientRect();
      setContextMenu({
        visible: true,
        x: rect ? event.clientX - rect.left : event.clientX,
        y: rect ? event.clientY - rect.top : event.clientY,
        type: "node",
        data: node,
      });
    },
    [],
  );

  const onEdgeContextMenu = useCallback(
    (event: React.MouseEvent, edge: Edge) => {
      console.log("[LLMVisualEditor] onEdgeContextMenu fired:", edge.id, (edge as Edge<VisualEdgeData>).data?.kind);
      event.preventDefault();
      event.stopPropagation();
      const rect = editorContainerRef.current?.getBoundingClientRect();
      setContextMenu({
        visible: true,
        x: rect ? event.clientX - rect.left : event.clientX,
        y: rect ? event.clientY - rect.top : event.clientY,
        type: "edge",
        data: edge,
      });
    },
    [],
  );

  const onPaneContextMenu = useCallback(
    (event: MouseEvent | React.MouseEvent<Element, MouseEvent>) => {
      console.log("[LLMVisualEditor] onPaneContextMenu fired (right-click on empty area)");
      event.preventDefault();
      setContextMenu(null);
    },
    [],
  );

  const onPaneClick = useCallback(() => {
    console.log("[LLMVisualEditor] onPaneClick fired — clearing context menu");
    setContextMenu(null);
  }, []);

  const closeContextMenu = useCallback(() => {
    console.log("[LLMVisualEditor] closeContextMenu called");
    setContextMenu(null);
  }, []);

  const focusNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId);
      if (node && rfInstance) {
        rfInstance.setCenter(node.position.x, node.position.y, {
          zoom: 1.2,
          duration: 400,
        });
      }
    },
    [nodes, rfInstance],
  );

  const handleAutoLayout = useCallback(() => {
    const updates: Node<VisualNodeData>[] = [];

    // Group nodes by type
    const providers = nodes.filter((n) => n.type === "provider");
    const models = nodes.filter((n) => n.type === "model");
    const roles = nodes.filter((n) => n.type === "role");
    const others = nodes.filter(
      (n) => !["provider", "model", "role"].includes(n.type || ""),
    );

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
  const getContextMenuItems = useCallback((): {
    items: ContextMenuItem[];
    title?: string;
  } => {
    if (!contextMenu) return { items: [] };

    if (contextMenu.type === "node") {
      const node = contextMenu.data as Node<VisualNodeData>;
      const items: ContextMenuItem[] = [];
      let title = "";

      if (node.type === "provider") {
        const data = node.data as VisualProviderNodeData;
        title = `提供商：${data.label}`;
        items.push({
          label: "测试连接",
          icon: Activity,
          action: () => {
            devLogger.debug("Test provider", data.providerId);
          },
        });
        items.push({
          label: "删除 Provider",
          icon: Trash2,
          variant: "danger",
          action: () => deleteNode(node.id),
        });
      } else if (node.type === "model") {
        const data = node.data as VisualModelNodeData;
        title = `模型：${data.model}`;
        items.push({
          label: "删除模型",
          icon: Trash2,
          variant: "danger",
          action: () => deleteNode(node.id),
        });
      } else if (node.type === "role") {
        const data = node.data as VisualRoleNodeData;
        title = `角色：${data.label}`;
        items.push({
          label: "清除分配",
          icon: Unplug,
          variant: "warning",
          action: () => clearRoleAssignment(data.roleId),
        });
      }
      return { items, title };
    } else if (contextMenu.type === "edge") {
      const edge = contextMenu.data as Edge<VisualEdgeData>;
      const edgeKind = edge.data?.kind;
      // Only model-to-role edges can be deleted (they correspond to a role binding
      // in the config). Provider-to-model edges are structural — deleting the
      // model node (via its context menu) is the correct way to remove a model.
      if (edgeKind === "model-to-role") {
        return {
          title: "连接操作",
          items: [
            {
              label: "删除连接",
              icon: Unplug,
              variant: "danger",
              action: () => deleteEdge(edge.id),
            },
          ],
        };
      }
      // Provider-to-model or unknown edge — show informational items
      const items: ContextMenuItem[] = [];
      const sourceNode = nodes.find((n) => n.id === edge.source);
      if (sourceNode?.type === "provider") {
        const targetNode = nodes.find((n) => n.id === edge.target);
        if (targetNode?.type === "model") {
          items.push({
            label: "删除模型（含连接）",
            icon: Trash2,
            variant: "danger",
            action: () => deleteNode(targetNode.id),
          });
        }
      }
      return {
        title: edgeKind === "provider-to-model" ? "提供商 → 模型" : "连接操作",
        items: items.length > 0 ? items : [
          {
            label: "此连接不可直接删除",
            icon: Unplug,
            disabled: true,
            action: () => {},
          },
        ],
      };
    }
    return { items: [] };
  }, [clearRoleAssignment, contextMenu, deleteNode, deleteEdge, nodes]);

  const contextMenuView = useMemo(
    () => getContextMenuItems(),
    [getContextMenuItems],
  );

  // DEBUG: track contextMenu state lifecycle
  useEffect(() => {
    if (contextMenu) {
      console.log("[LLMVisualEditor] useEffect: contextMenu SET", { type: contextMenu.type, items: contextMenuView.items.length, x: contextMenu.x, y: contextMenu.y });
    } else {
      console.log("[LLMVisualEditor] useEffect: contextMenu CLEARED");
    }
  }, [contextMenu, contextMenuView]);

  useEffect(() => {
    if (!providerDraft && providers.length > 0) {
      setProviderDraft(providers[0][0]);
    }
  }, [providerDraft, providers]);

  const handleAddModel = () => {
    const modelName = modelDraft.trim();
    if (!modelName || !providerDraft) return;
    addModel(providerDraft, modelName);
    setModelDraft("");
  };

  const handleProviderConcurrencyChange = (
    providerId: string,
    rawValue: string,
  ) => {
    commitConfigUpdate((current) =>
      updateProviderConcurrency(current, providerId, readPositiveInt(rawValue)),
    );
  };

  const handleRoleConcurrencyChange = (
    roleId: VisualRoleId,
    rawValue: string,
  ) => {
    commitConfigUpdate((current) =>
      updateRoleConcurrency(current, roleId, readPositiveInt(rawValue)),
    );
  };

  const handleBindingConcurrencyChange = (
    roleId: VisualRoleId,
    providerId: string,
    model: string,
    rawValue: string,
  ) => {
    commitConfigUpdate((current) =>
      updateRoleBindingConcurrency(
        current,
        roleId,
        providerId,
        model,
        readPositiveInt(rawValue),
      ),
    );
  };

  const isValid = useCallback(
    (connection: Connection | Edge) => {
      return isValidVisualConnection(connection, nodes);
    },
    [nodes],
  );

  const nodeColor = (node: Node<VisualNodeData>) => {
    if (node.type === "role") return "#22d3ee";
    if (node.type === "provider") return "#f472b6";
    return "#34d399";
  };

  const handleNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChange(changes);
    },
    [onNodesChange],
  );

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
      className="relative soft-panel rounded-xl p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div>
          <div className="text-xs font-semibold text-text-main">
            LLM 视觉配置编辑器 · 吏部·铨选司
          </div>
          <div className="text-[10px] text-text-dim">
            拖拽连线：提供商 → 模型 → 角色
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleAutoLayout}
            className="p-1.5 text-text-dim hover:text-text-main transition-colors"
            title="自动布局"
          >
            <LayoutGrid size={14} />
          </button>
          <button
            type="button"
            onClick={() => rfInstance?.fitView({ duration: 400 })}
            className="p-1.5 text-text-dim hover:text-text-main transition-colors"
            title="适应视图"
          >
            <Maximize size={14} />
          </button>
          <div className="w-px h-3 bg-white/10 mx-1" />
          <button
            type="button"
            onClick={() => setShowAddModel((prev) => !prev)}
            className="px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded transition-colors"
          >
            添加模型
          </button>
          {onSave ? (
            <button
              type="button"
              data-testid="llm-visual-save"
              onClick={handleSave}
              className="px-3 py-1.5 text-[10px] font-semibold bg-emerald-500/[0.15] hover:bg-emerald-500/25 text-emerald-200 rounded transition-colors"
            >
              保存配置
            </button>
          ) : null}
          {validation.issues.length > 0 && (
            <button
              type="button"
              onClick={() => setShowValidationPanel((v) => !v)}
              className="ml-2"
            >
              <ValidationBadge count={validation.issues.length} />
            </button>
          )}
        </div>
      </div>

      {showAddModel ? (
        <div className="mb-3 grid grid-cols-1 md:grid-cols-[180px_1fr_auto] gap-2 items-center soft-panel-subtle rounded-lg p-2">
          <select
            className="soft-inset text-[10px] text-text-main rounded px-2 py-1.5"
            value={providerDraft}
            onChange={(event) => setProviderDraft(event.target.value)}
          >
            <option value="">选择提供商</option>
            {providers.map(([providerId, provider]) => {
              const label =
                typeof provider === "object" &&
                provider !== null &&
                "name" in provider
                  ? String(
                      (provider as Record<string, unknown>).name || providerId,
                    )
                  : providerId;
              return (
                <option key={providerId} value={providerId}>
                  {label}
                </option>
              );
            })}
          </select>
          <input
            className="soft-inset text-[10px] text-text-main rounded px-2 py-1.5"
            placeholder="模型名称"
            value={modelDraft}
            onChange={(event) => setModelDraft(event.target.value)}
          />
          <button
            type="button"
            onClick={handleAddModel}
            className="px-3 py-1.5 text-[10px] font-semibold bg-white/[0.12] hover:bg-white/[0.16] text-text-main rounded"
          >
            添加
          </button>
        </div>
      ) : null}

      <div className="mb-3 rounded-lg border border-white/10 bg-black/15 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <SlidersHorizontal size={14} className="text-cyan-300" />
            <div className="min-w-0">
              <div className="text-[10px] font-semibold text-text-main">
                并发容量
              </div>
              <div className="truncate text-[10px] text-text-dim">
                Provider 上限 ∩ Role 上限 ∩ Binding 上限
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded border border-cyan-400/20 bg-cyan-400/10 px-2 py-1 text-[10px] text-cyan-100">
              Provider {providers.length}
            </span>
            <span className="rounded border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-100">
              Role {roleConcurrencyRows.length}
            </span>
            <span className="rounded border border-fuchsia-400/20 bg-fuchsia-400/10 px-2 py-1 text-[10px] text-fuchsia-100">
              Binding {bindingRows.length}
            </span>
            <button
              type="button"
              onClick={() => setShowConcurrencyPanel((value) => !value)}
              className="inline-flex items-center gap-1 rounded border border-white/10 bg-white/[0.08] px-2.5 py-1 text-[10px] font-semibold text-text-main transition-colors hover:bg-white/[0.14]"
              data-testid="llm-visual-toggle-concurrency"
            >
              {showConcurrencyPanel ? (
                <>
                  收起
                  <ChevronUp size={12} />
                </>
              ) : (
                <>
                  展开
                  <ChevronDown size={12} />
                </>
              )}
            </button>
          </div>
        </div>

        {showConcurrencyPanel ? (
          <div className="mt-3 grid grid-cols-1 gap-3 border-t border-white/10 pt-3 xl:grid-cols-3">
            <div className="min-w-0">
              <div className="mb-1 text-[10px] font-semibold text-text-dim">
                Provider
              </div>
              <div className="max-h-28 space-y-1 overflow-y-auto pr-1">
                {providers.map(([providerId, provider]) => {
                  const providerCfg =
                    typeof provider === "object" && provider !== null
                      ? (provider as Record<string, unknown>)
                      : {};
                  const value =
                    typeof providerCfg.max_concurrency === "number"
                      ? providerCfg.max_concurrency
                      : "";
                  return (
                    <label
                      key={providerId}
                      className="grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim"
                    >
                      <span className="truncate text-text-main">
                        {providerId}
                      </span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={value}
                        onChange={(event) =>
                          handleProviderConcurrencyChange(
                            providerId,
                            event.target.value,
                          )
                        }
                        className="w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main"
                        placeholder="auto"
                      />
                    </label>
                  );
                })}
              </div>
            </div>

            <div className="min-w-0">
              <div className="mb-1 text-[10px] font-semibold text-text-dim">
                Role
              </div>
              <div className="max-h-28 space-y-1 overflow-y-auto pr-1">
                {roleConcurrencyRows.map(({ roleId, roleCfg }) => {
                  const value =
                    typeof roleCfg.max_concurrency === "number"
                      ? roleCfg.max_concurrency
                      : typeof roleCfg.concurrency === "number"
                        ? roleCfg.concurrency
                        : "";
                  return (
                    <label
                      key={roleId}
                      className="grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim"
                    >
                      <span className="truncate text-text-main">
                        {getLlmRoleDefinition(roleId).label}
                      </span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={value}
                        onChange={(event) =>
                          handleRoleConcurrencyChange(
                            roleId,
                            event.target.value,
                          )
                        }
                        className="w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main"
                        placeholder="1"
                      />
                    </label>
                  );
                })}
                {roleConcurrencyRows.length === 0 ? (
                  <div className="text-[10px] text-text-dim">
                    暂无 Role 绑定
                  </div>
                ) : null}
              </div>
            </div>

            <div className="min-w-0">
              <div className="mb-1 text-[10px] font-semibold text-text-dim">
                Binding
              </div>
              <div className="max-h-28 space-y-1 overflow-y-auto pr-1">
                {bindingRows.map(({ roleId, binding, index }) => {
                  const value =
                    typeof binding.max_concurrency === "number"
                      ? binding.max_concurrency
                      : typeof binding.concurrency === "number"
                        ? binding.concurrency
                        : "";
                  return (
                    <label
                      key={`${roleId}:${binding.provider_id}:${binding.model}:${index}`}
                      className="grid grid-cols-[minmax(0,1fr)_72px] items-center gap-2 text-[10px] text-text-dim"
                    >
                      <span className="truncate text-text-main">
                        {getLlmRoleDefinition(roleId).label} ·{" "}
                        {binding.provider_id}/{binding.model}
                      </span>
                      <input
                        type="number"
                        min="1"
                        step="1"
                        value={value}
                        onChange={(event) =>
                          handleBindingConcurrencyChange(
                            roleId,
                            binding.provider_id,
                            binding.model,
                            event.target.value,
                          )
                        }
                        className="w-full rounded soft-inset px-2 py-1 text-[10px] text-text-main"
                        placeholder="auto"
                      />
                    </label>
                  );
                })}
                {bindingRows.length === 0 ? (
                  <div className="text-[10px] text-text-dim">
                    暂无 Binding
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div
        className="h-[60vh] min-h-[520px] soft-inset rounded-xl overflow-hidden"
      >
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
          onPaneContextMenu={onPaneContextMenu}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          isValidConnection={isValid}
          className="bg-transparent"
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
        <div
          data-testid="llm-visual-context-menu"
          style={{
            position: "absolute",
            top: `${contextMenu.y}px`,
            left: `${contextMenu.x}px`,
            zIndex: 99999,
            minWidth: "180px",
            background: "#1e293b",
            border: "1px solid #475569",
            borderRadius: "8px",
            padding: "4px",
            boxShadow: "0 10px 40px rgba(0,0,0,0.5)",
          }}
          onContextMenu={(e) => e.preventDefault()}
        >
          {contextMenuView.title && (
            <div style={{ borderBottom: "1px solid rgba(255,255,255,0.1)", padding: "8px 12px", fontSize: "12px", fontWeight: 600, color: "#94a3b8" }}>
              {contextMenuView.title}
            </div>
          )}
          <div style={{ padding: "4px" }}>
            {contextMenuView.items.map((item, index) => {
              const Icon = item.icon;
              const isDanger = item.variant === "danger";
              return (
                <button
                  key={index}
                  type="button"
                  data-testid={`llm-visual-context-menu-item-${item.label}`}
                  onClick={() => {
                    console.log("[LLMVisualEditor] menu item clicked:", item.label);
                    if (!item.disabled) {
                      item.action();
                      closeContextMenu();
                    }
                  }}
                  disabled={item.disabled}
                  style={{
                    display: "flex",
                    width: "100%",
                    alignItems: "center",
                    gap: "8px",
                    padding: "6px 8px",
                    fontSize: "12px",
                    borderRadius: "4px",
                    border: "none",
                    background: "transparent",
                    cursor: item.disabled ? "not-allowed" : "pointer",
                    color: item.disabled ? "#475569" : isDanger ? "#f87171" : "#e2e8f0",
                    opacity: item.disabled ? 0.5 : 1,
                  }}
                  onMouseEnter={(e) => { if (!item.disabled) e.currentTarget.style.background = isDanger ? "rgba(239,68,68,0.2)" : "rgba(255,255,255,0.1)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  {Icon && <Icon size={14} />}
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
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
