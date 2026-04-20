import { useCallback, useEffect, useMemo, useState, useRef } from 'react';
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from '@xyflow/react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import type { VisualEdgeData, VisualGraphConfig, VisualGraphStatus, VisualNodeData, VisualRoleId } from '../types/visual';
import {
  addManualModel,
  buildVisualGraph,
  clearRoleAssignment,
  extractNodePositions,
  mergeNodePositionsWithStates,
  modelNodeId,
  removeManualModel,
  removeProvider,
  restoreNodeStates,
  updateRoleAssignment,
  updateVisualStates,
} from '../utils/configConverter';

interface UseVisualLLMConfigOptions {
  config: VisualGraphConfig | null;
  status?: VisualGraphStatus | null;
  onConfigChange?: (config: VisualGraphConfig) => void;
}

interface RuntimeRoleStatus {
  running: boolean;
  startedAt?: string;
  lastRun?: string;
  lastStatus?: string;
  lastError?: string;
  config: {
    provider_id?: string;
    model?: string;
    profile?: string;
  };
}

interface RuntimeStatus {
  roles: Record<string, RuntimeRoleStatus>;
  timestamp: string;
}

const isSameRuntimeStatus = (
  previous: RuntimeRoleStatus | undefined,
  next: RuntimeRoleStatus | undefined
): boolean => {
  if (!previous && !next) return true;
  if (!previous || !next) return false;
  return (
    previous.running === next.running &&
    previous.startedAt === next.startedAt &&
    previous.lastRun === next.lastRun &&
    previous.lastStatus === next.lastStatus &&
    previous.lastError === next.lastError &&
    previous.config?.provider_id === next.config?.provider_id &&
    previous.config?.model === next.config?.model &&
    previous.config?.profile === next.config?.profile
  );
};

const applyRuntimeStatusToNodes = (
  currentNodes: Node<VisualNodeData>[],
  runtimeStatus: RuntimeStatus | null
): Node<VisualNodeData>[] => {
  if (!runtimeStatus?.roles) {
    return currentNodes;
  }

  let changed = false;
  const nextNodes = currentNodes.map((node) => {
    if (node.type !== 'role' || node.data.kind !== 'role') {
      return node;
    }
    const incomingStatus = runtimeStatus.roles[node.data.roleId];
    const currentStatus = node.data.runtimeStatus;
    if (isSameRuntimeStatus(currentStatus, incomingStatus)) {
      return node;
    }
    changed = true;
    return {
      ...node,
      data: {
        ...node.data,
        runtimeStatus: incomingStatus,
      },
    };
  });

  return changed ? nextNodes : currentNodes;
};

export function useVisualLLMConfig({ config, status, onConfigChange }: UseVisualLLMConfigOptions) {
  const graph = useMemo(() => {
    if (!config) return { nodes: [], edges: [] };
    return buildVisualGraph(config, status || undefined);
  }, [config, status]);

  const [nodes, setNodes] = useState<Node<VisualNodeData>[]>(graph.nodes);
  const [edges, setEdges] = useState<Edge<VisualEdgeData>[]>(graph.edges);
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);

  const latestConfigRef = useRef<VisualGraphConfig | null>(config);
  const onConfigChangeRef = useRef(onConfigChange);

  useEffect(() => {
    latestConfigRef.current = config;
  }, [config]);

  useEffect(() => {
    onConfigChangeRef.current = onConfigChange;
  }, [onConfigChange]);

  useEffect(() => {
    setNodes((currentNodes) => applyRuntimeStatusToNodes(currentNodes, runtimeStatus));
  }, [runtimeStatus]);

  // 使用一次性获取代替轮询 - 运行时状态应该在初始加载时获取
  // 如果需要实时更新，应该通过 WebSocket 推送而不是轮询
  useEffect(() => {
    let disposed = false;

    const fetchRuntimeStatus = async () => {
      try {
        const response = await apiFetch('/llm/runtime-status');
        if (!response.ok || disposed) return;
        const data = (await response.json()) as RuntimeStatus;
        if (!disposed) {
          setRuntimeStatus(data);
        }
      } catch (error) {
        if (!disposed) {
          devLogger.debug('Failed to fetch runtime status:', error);
        }
      }
    };

    // 仅在组件挂载时获取一次运行时状态
    // 如果需要实时更新，应通过 WebSocket 推送实现
    void fetchRuntimeStatus();

    return () => {
      disposed = true;
    };
  }, []);

  useEffect(() => {
    setNodes((currentNodes) => {
      let rebuiltNodes = graph.nodes;
      if (config?.visual_node_states) {
        rebuiltNodes = restoreNodeStates(rebuiltNodes, config.visual_node_states || {});
      }
      return mergeNodePositionsWithStates(currentNodes, rebuiltNodes, config?.visual_node_states || {});
    });
    setEdges(graph.edges);
  }, [graph.nodes, graph.edges, config?.visual_node_states]);

  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((current) => applyEdgeChanges(changes, current) as Edge<VisualEdgeData>[]);
  }, []);

  // 同步节点位置到配置
  const syncNodePositions = useCallback(
    (currentConfig: VisualGraphConfig, currentNodes: Node<VisualNodeData>[]) => {
      const onChange = onConfigChangeRef.current;
      if (!onChange) return;
      const layout = extractNodePositions(currentNodes);
      const nextConfig = {
        ...currentConfig,
        visual_layout: layout,
      };
      latestConfigRef.current = nextConfig;
      onChange(nextConfig);
    },
    []
  );

  // 同步完整的节点状态到配置
  const syncNodeStates = useCallback(
    (currentConfig: VisualGraphConfig, currentNodes: Node<VisualNodeData>[], currentEdges: Edge<VisualEdgeData>[]) => {
      const onChange = onConfigChangeRef.current;
      if (!onChange) return;
      const nextConfig = updateVisualStates(currentConfig, currentNodes, currentEdges);
      latestConfigRef.current = nextConfig;
      onChange(nextConfig);
    },
    []
  );

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((current) => applyNodeChanges(changes, current) as Node<VisualNodeData>[]);
    },
    []
  );

  const updateConfigRole = useCallback(
    (roleId: VisualRoleId, providerId: string, model: string) => {
      const currentConfig = latestConfigRef.current;
      const onChange = onConfigChangeRef.current;
      if (!currentConfig || !onChange) return;
      const nextConfig = updateRoleAssignment(currentConfig, roleId, providerId, model);
      latestConfigRef.current = nextConfig;
      onChange(nextConfig);
    },
    []
  );

  const clearConfigRole = useCallback(
    (roleId: VisualRoleId) => {
      const currentConfig = latestConfigRef.current;
      const onChange = onConfigChangeRef.current;
      if (!currentConfig || !onChange) return;
      const nextConfig = clearRoleAssignment(currentConfig, roleId);
      latestConfigRef.current = nextConfig;
      onChange(nextConfig);
    },
    []
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return;
      const sourceNode = nodeMap.get(connection.source);
      const targetNode = nodeMap.get(connection.target);
      if (!sourceNode || !targetNode) return;

      if (sourceNode.type === 'model' && targetNode.type === 'role') {
        const modelData = sourceNode.data;
        const roleData = targetNode.data;
        if (modelData.kind !== 'model' || roleData.kind !== 'role') return;
        updateConfigRole(roleData.roleId, modelData.providerId, modelData.model);
        setEdges((current) => {
          const filtered = current.filter(
            (edge) => !(edge.data?.kind === 'model-to-role' && edge.target === targetNode.id)
          );
          const exists = filtered.some(
            (edge) => edge.source === connection.source && edge.target === connection.target
          );
          if (exists) return filtered;
          return addEdge(
            {
              ...connection,
              type: 'custom',
              data: { kind: 'model-to-role' },
            },
            filtered
          );
        });
      } else if (sourceNode.type === 'provider' && targetNode.type === 'model') {
        setEdges((current) => {
          const exists = current.some(
            (edge) => edge.source === connection.source && edge.target === connection.target
          );
          if (exists) return current;
          return addEdge(
            {
              ...connection,
              type: 'custom',
              data: { kind: 'provider-to-model' },
            },
            current
          );
        });
      } else if (sourceNode.type === 'provider' && targetNode.type === 'role') {
        // Direct Provider -> Role connection
        // Auto-resolve model to bridge the connection
        const providerData = sourceNode.data;
        const roleData = targetNode.data;
        
        if (providerData.kind === 'provider' && roleData.kind === 'role') {
          const providerId = providerData.providerId;
          const roleId = roleData.roleId;

          let targetModel = '';

          // 1. Try to find model from provider config
          const currentConfig = latestConfigRef.current;
          if (currentConfig && currentConfig.providers) {
            const providerCfg = currentConfig.providers[providerId] as Record<string, unknown> | undefined;
            if (providerCfg && typeof providerCfg === 'object') {
              if (typeof providerCfg.default_model === 'string' && providerCfg.default_model) {
                targetModel = providerCfg.default_model;
              } else if (typeof providerCfg.model === 'string' && providerCfg.model) {
                targetModel = providerCfg.model;
              }
            }
          }

          // 2. If no config model, try to use first existing model node for this provider
          if (!targetModel) {
            const existingModelNode = nodes.find(
              (n) => n.type === 'model' && n.data.kind === 'model' && n.data.providerId === providerId
            );
            if (existingModelNode && existingModelNode.data.kind === 'model') {
              targetModel = existingModelNode.data.model;
            }
          }

          // 3. Fallback: if we found a model (or if we want to force one?), update config
          if (targetModel) {
             updateConfigRole(roleId, providerId, targetModel);
          } else {
             // If we can't resolve a model, we can't create a valid 3-way link.
             // But maybe we should just create a placeholder model node?
             // For now, let's warn. Ideally we'd prompt user.
             devLogger.warn('Could not auto-resolve model for provider -> role connection');
             
             // Optional: If it's a known provider type like Ollama without a model selected yet,
             // maybe we just default to 'latest'? 
             // But safer to do nothing if we can't be sure.
          }
        }
      }
    },
    [nodeMap, nodes, updateConfigRole]
  );

  const onEdgesDelete = useCallback(
    (deleted: Edge<VisualEdgeData>[]) => {
      deleted.forEach((edge) => {
        if (edge.data?.kind !== 'model-to-role') return;
        const targetNode = nodeMap.get(edge.target);
        if (!targetNode || targetNode.type !== 'role') return;
        const roleData = targetNode.data;
        if (roleData.kind === 'role') {
          clearConfigRole(roleData.roleId);
        }
      });
    },
    [clearConfigRole, nodeMap]
  );

  const deleteNode = useCallback(
    (nodeId: string) => {
      const node = nodeMap.get(nodeId);
      const currentConfig = latestConfigRef.current;
      const onChange = onConfigChangeRef.current;
      if (!node || !currentConfig || !onChange) return;

      if (node.type === 'model') {
        const data = node.data;
        if (data.kind === 'model') {
          const nextConfig = removeManualModel(currentConfig, data.providerId, data.model);
          latestConfigRef.current = nextConfig;
          onChange(nextConfig);
          setNodes((current) => current.filter((item) => item.id !== nodeId));
          setEdges((current) =>
            current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId)
          );
        }
      } else if (node.type === 'provider') {
        const data = node.data;
        if (data.kind === 'provider') {
          const providerId = data.providerId;
          const removableNodeIds = new Set(
            Array.from(nodeMap.values())
              .filter(
                (item) =>
                  item.id === nodeId ||
                  (item.type === 'model' &&
                    item.data.kind === 'model' &&
                    item.data.providerId === providerId)
              )
              .map((item) => item.id)
          );
          const nextConfig = removeProvider(currentConfig, providerId);
          latestConfigRef.current = nextConfig;
          onChange(nextConfig);
          setNodes((current) => current.filter((item) => !removableNodeIds.has(item.id)));
          setEdges((current) =>
            current.filter(
              (edge) => !removableNodeIds.has(edge.source) && !removableNodeIds.has(edge.target)
            )
          );
        }
      } else if (node.type === 'role') {
         // Roles cannot be deleted from visual editor usually (defined in backend/config structure), 
         // but maybe we can clear its assignment?
         // For now, allow clearing assignment via context menu action "Clear Assignment", 
         // but "Delete" might not mean deleting the role itself.
         // Let's supported clearing assignment if "delete" is called on role?
         // Or just do nothing for now.
      }
    },
    [nodeMap]
  );

  const deleteEdge = useCallback(
    (edgeId: string) => {
      const edge = edges.find(e => e.id === edgeId);
      if (!edge) return;
      // Re-use onEdgesDelete logic
      onEdgesDelete([edge]);
      // Also update local state for immediate feedback
      setEdges((prev) => prev.filter((e) => e.id !== edgeId));
    },
    [edges, onEdgesDelete]
  );

  const addModel = useCallback(
    (providerId: string, model: string) => {
      if (!providerId || !model) return;
      const existing = nodes.find(
        (node) => node.type === 'model' && node.data.kind === 'model' && node.data.providerId === providerId && node.data.model === model
      );
      if (existing) return;
      const providerNode = nodes.find(
        (node) =>
          node.type === 'provider' &&
          node.data.kind === 'provider' &&
          node.data.providerId === providerId
      );
      if (!providerNode) return;
      const nextNode: Node<VisualNodeData> = {
        id: modelNodeId(providerId, model),
        type: 'model',
        position: { x: providerNode.position.x + 300, y: providerNode.position.y + 140 },
        data: {
          kind: 'model',
          providerId,
          model,
          label: model,
          assignedRoles: [],
        },
      };
      setNodes((current) => [...current, nextNode]);
      setEdges((current) => [
        ...current,
        {
          id: `edge:${providerNode.id}:${nextNode.id}`,
          source: providerNode.id,
          target: nextNode.id,
          type: 'custom',
          data: { kind: 'provider-to-model' },
        },
      ]);
      const currentConfig = latestConfigRef.current;
      const onChange = onConfigChangeRef.current;
      if (currentConfig && onChange) {
        const providerCfg = currentConfig.providers?.[providerId] as Record<string, unknown> | undefined;
        const providerType = typeof providerCfg?.type === 'string' ? providerCfg.type : '';
        if (providerType === 'codex_cli' || providerType === 'gemini_cli' || providerType === 'cli') {
          const nextConfig = addManualModel(currentConfig, providerId, model);
          latestConfigRef.current = nextConfig;
          onChange(nextConfig);
        }
      }
    },
    [nodes]
  );

  const onNodesDelete = useCallback(
    (deleted: Node<VisualNodeData>[]) => {
      deleted.forEach((node) => {
        deleteNode(node.id);
      });
    },
    [deleteNode]
  );

  const getCurrentConfig = useCallback(() => latestConfigRef.current, []);

  return {
    nodes,
    edges,
    onNodesChange,
    onEdgesChange,
    onNodesDelete,
    onConnect,
    onEdgesDelete,
    addModel,
    clearRoleAssignment: clearConfigRole,
    syncNodePositions,
    syncNodeStates,
    getCurrentConfig,
    setNodes,
    setEdges,
    deleteNode,
    deleteEdge,
    runtimeStatus,
  };
}
