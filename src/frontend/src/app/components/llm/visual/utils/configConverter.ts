import type { Edge, Node } from '@xyflow/react';
import type {
  VisualEdgeData,
  VisualGraphConfig,
  VisualGraphStatus,
  VisualModelNodeData,
  VisualNodeData,
  VisualNodePosition,
  VisualNodeState,
  VisualProviderNodeData,
  VisualRoleId,
  VisualRoleNodeData,
} from '../types/visual';

const ROLE_ORDER: VisualRoleId[] = [
  'pm',
  'director',
  'chief_engineer',
  'qa',
  'architect',
  'cfo',
  'hr',
];

const ROLE_META: Record<VisualRoleId, { label: string; description: string }> = {
  pm: { label: '尚书令', description: '承受诏旨，统筹章奏、节次与推进。' },
  director: { label: '工部侍郎', description: '奉诏动工，负责实现、调度与技术裁断（实际编码）。' },
  chief_engineer: { label: '工部尚书', description: '绘制《营造法式》蓝图，定体例与纲目（设计不编码）。' },
  qa: { label: '门下侍中', description: '主司封驳与勘验，确保证据链完备。' },
  architect: { label: '中书令', description: '草拟项目规格与架构文档。' },
  cfo: { label: '户部尚书', description: '核算预算，监控Token用量与成本。' },
  hr: { label: '吏部尚书', description: '管理LLM配置与人员（模型）任免。' },
};

const encodeNodeSegment = (value: string) => encodeURIComponent(value);
const legacyNormalizeId = (value: string) => value.replace(/[^a-zA-Z0-9_-]/g, '_');
const legacyProviderNodeId = (providerId: string) => `provider:${legacyNormalizeId(providerId)}`;
const legacyModelNodeId = (providerId: string, model: string) =>
  `model:${legacyNormalizeId(providerId)}:${legacyNormalizeId(model)}`;

export const roleNodeId = (roleId: VisualRoleId) => `role:${roleId}`;
const normalizeVisualRoleId = (roleId: string): VisualRoleId | null => {
  if (roleId === 'docs') return 'architect';
  if ((ROLE_ORDER as string[]).includes(roleId)) return roleId as VisualRoleId;
  return null;
};
export const providerNodeId = (providerId: string) => `provider:${encodeNodeSegment(providerId)}`;
export const modelNodeId = (providerId: string, model: string) =>
  `model:${encodeNodeSegment(providerId)}:${encodeNodeSegment(model)}`;

const coerceManualModels = (config: Record<string, unknown>) => {
  const manual = config.manual_models;
  if (Array.isArray(manual)) {
    return manual.map((item) => String(item)).filter(Boolean);
  }
  return [];
};

export const buildVisualGraph = (
  config: VisualGraphConfig,
  status?: VisualGraphStatus | null
): { nodes: Node<VisualNodeData>[]; edges: Edge<VisualEdgeData>[] } => {
  const providers = Object.entries(config.providers || {});
  const roleReqs = config.policies?.role_requirements || {};
  const savedLayout = config.visual_layout || {};

  // Helper to safely restore position
  const restorePosition = (
    nodeId: string,
    defaultPosition: VisualNodePosition,
    fallbackNodeIds: string[] = []
  ): VisualNodePosition => {
    const ids = [nodeId, ...fallbackNodeIds];
    for (const id of ids) {
      const saved = savedLayout[id];
      if (saved && typeof saved.x === 'number' && typeof saved.y === 'number') {
        return saved;
      }
    }
    return defaultPosition;
  };

  const providerModels = new Map<string, Set<string>>();
  const addModel = (providerId: string, model: string) => {
    if (!providerId || !model) return;
    if (!providerModels.has(providerId)) {
      providerModels.set(providerId, new Set());
    }
    providerModels.get(providerId)?.add(model);
  };

  Object.entries(config.roles || {}).forEach(([roleId, roleCfg]) => {
    const providerId = roleCfg?.provider_id || '';
    const model = roleCfg?.model || '';
    if (providerId && model) {
      addModel(providerId, model);
    }
  });

  providers.forEach(([providerId, providerCfgRaw]) => {
    const providerCfg =
      typeof providerCfgRaw === 'object' && providerCfgRaw !== null
        ? (providerCfgRaw as Record<string, unknown>)
        : {};
    const configuredModels = [providerCfg.default_model, providerCfg.model]
      .filter((item): item is string => typeof item === 'string')
      .map((item) => item.trim())
      .filter(Boolean);
    configuredModels.forEach((model) => addModel(providerId, model));
    const manualModels = coerceManualModels(providerCfg);
    manualModels.forEach((model) => addModel(providerId, model));
  });

  const nodes: Node<VisualNodeData>[] = [];
  const edges: Edge<VisualEdgeData>[] = [];

  providers.forEach(([providerId, providerCfgRaw], providerIndex) => {
    const providerCfg = typeof providerCfgRaw === 'object' && providerCfgRaw !== null
      ? (providerCfgRaw as Record<string, unknown>)
      : {};
    const providerType = typeof providerCfg.type === 'string' ? providerCfg.type : undefined;
    const providerLabel =
      typeof providerCfg.name === 'string' && providerCfg.name.trim()
        ? providerCfg.name.trim()
        : providerId;
    const rawProviderStatus = status?.providers?.[providerId]?.status;
    const providerStatus =
      rawProviderStatus === 'running' ||
      rawProviderStatus === 'success' ||
      rawProviderStatus === 'failed' ||
      rawProviderStatus === 'unknown'
        ? rawProviderStatus
        : 'unknown';
    const modelList = Array.from(providerModels.get(providerId) || []);
    
    const providerIdValue = providerNodeId(providerId);
    const providerNode: Node<VisualProviderNodeData> = {
      id: providerIdValue,
      type: 'provider',
      position: restorePosition(
        providerIdValue,
        { x: 40, y: providerIndex * 180 + 40 },
        [legacyProviderNodeId(providerId)]
      ),
      data: {
        kind: 'provider',
        providerId,
        label: providerLabel,
        providerType,
        status: providerStatus,
        modelCount: modelList.length,
      },
    };
    nodes.push(providerNode);

    modelList.forEach((model, modelIndex) => {
      const modelId = modelNodeId(providerId, model);
      const modelNode: Node<VisualModelNodeData> = {
        id: modelId,
        type: 'model',
        position: restorePosition(
          modelId,
          { x: 340, y: providerIndex * 180 + modelIndex * 120 + 40 },
          [legacyModelNodeId(providerId, model)]
        ),
        data: {
          kind: 'model',
          providerId,
          model,
          label: model,
          assignedRoles: [],
        },
      };
      nodes.push(modelNode);
      edges.push({
        id: `edge:${providerNode.id}:${modelNode.id}`,
        source: providerNode.id,
        target: modelNode.id,
        type: 'custom',
        data: { kind: 'provider-to-model' },
      });
    });
  });

  ROLE_ORDER.forEach((roleId, index) => {
    const requirement = roleReqs[roleId] || {};
    const readiness = status?.roles?.[roleId];
    const meta = ROLE_META[roleId];
    
    nodes.push({
      id: roleNodeId(roleId),
      type: 'role',
      position: restorePosition(roleNodeId(roleId), { x: 700, y: index * 180 + 40 }),
      data: {
        kind: 'role',
        roleId,
        label: meta.label,
        description: meta.description,
        requiresThinking: Boolean(requirement.requires_thinking),
        minConfidence: typeof requirement.min_confidence === 'number' ? requirement.min_confidence : undefined,
        readiness: readiness
          ? {
              ready: readiness.ready,
              grade: readiness.grade,
            }
          : undefined,
      },
    });
  });

  Object.entries(config.roles || {}).forEach(([roleId, roleCfg]) => {
    const providerId = roleCfg?.provider_id || '';
    const model = roleCfg?.model || '';
    if (!providerId || !model) return;
    const modelId = modelNodeId(providerId, model);
    const roleIdNormalized = normalizeVisualRoleId(roleId);
    if (!roleIdNormalized) return;
    const modelNode = nodes.find((node) => node.id === modelId);
    if (modelNode && modelNode.type === 'model') {
      const data = modelNode.data as VisualModelNodeData;
      data.assignedRoles = Array.from(new Set([...(data.assignedRoles || []), roleIdNormalized]));
    }
    edges.push({
      id: `edge:${modelId}:${roleNodeId(roleIdNormalized)}`,
      source: modelId,
      target: roleNodeId(roleIdNormalized),
      type: 'custom',
      data: { kind: 'model-to-role' },
    });
  });

  return { nodes, edges };
};

export const mergeNodePositions = (
  previous: Node<VisualNodeData>[],
  next: Node<VisualNodeData>[]
): Node<VisualNodeData>[] => {
  const positions = new Map(previous.map((node) => [node.id, node.position]));
  
  return next.map((node) => {
    const position = positions.get(node.id);
    return position ? { ...node, position } : node;
  });
};

export const mergeNodePositionsWithStates = (
  previous: Node<VisualNodeData>[],
  next: Node<VisualNodeData>[],
  savedStates: Record<string, VisualNodeState>
): Node<VisualNodeData>[] => {
  // Keep current in-memory node position first to avoid drag-reset during async refresh.
  const previousPositions = new Map(previous.map((node) => [node.id, node.position]));

  const resolveSavedState = (node: Node<VisualNodeData>): VisualNodeState | undefined => {
    const direct = savedStates[node.id];
    if (direct) return direct;
    if (node.type === 'provider' && node.data.kind === 'provider') {
      return savedStates[legacyProviderNodeId(node.data.providerId)];
    }
    if (node.type === 'model' && node.data.kind === 'model') {
      return savedStates[legacyModelNodeId(node.data.providerId, node.data.model)];
    }
    return undefined;
  };
  
  return next.map((node) => {
    // Prefer current position first.
    const previousPosition = previousPositions.get(node.id);
    if (previousPosition) {
      return { ...node, position: previousPosition };
    }
    // Fall back to saved layout/state position.
    const savedPosition = resolveSavedState(node)?.position;
    if (savedPosition) {
      return { ...node, position: savedPosition };
    }
    // 最后使用当前位置
    return node;
  });
};

export const updateRoleAssignment = (
  config: VisualGraphConfig,
  roleId: VisualRoleId,
  providerId: string,
  model: string
): VisualGraphConfig => {
  return {
    ...config,
    roles: {
      ...config.roles,
      [roleId]: {
        ...(config.roles?.[roleId] || {}),
        provider_id: providerId,
        model,
      },
    },
  };
};

export const clearRoleAssignment = (config: VisualGraphConfig, roleId: VisualRoleId): VisualGraphConfig => {
  const nextRole = { ...(config.roles?.[roleId] || {}) } as Record<string, unknown>;
  delete nextRole.provider_id;
  delete nextRole.model;
  return {
    ...config,
    roles: {
      ...config.roles,
      [roleId]: nextRole,
    },
  };
};

export const addManualModel = (
  config: VisualGraphConfig,
  providerId: string,
  model: string
): VisualGraphConfig => {
  const raw = config.providers?.[providerId];
  const providerCfg =
    typeof raw === 'object' && raw !== null ? ({ ...(raw as Record<string, unknown>) } as Record<string, unknown>) : {};
  const manualModels = coerceManualModels(providerCfg);
  if (!manualModels.includes(model)) {
    manualModels.push(model);
  }
  providerCfg.manual_models = manualModels;
  return {
    ...config,
    providers: {
      ...config.providers,
      [providerId]: providerCfg,
    },
  };
};

export const removeManualModel = (
  config: VisualGraphConfig,
  providerId: string,
  model: string
): VisualGraphConfig => {
  const raw = config.providers?.[providerId];
  if (!raw || typeof raw !== 'object') return config;
  
  const providerCfg = { ...(raw as Record<string, unknown>) } as Record<string, unknown>;
  const manualModels = coerceManualModels(providerCfg);
  const nextManual = manualModels.filter((m) => m !== model);
  
  providerCfg.manual_models = nextManual;

  // Clear roles using this model
  const nextRoles = { ...(config.roles || {}) };
  let rolesChanged = false;
  Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
    if (roleCfg.provider_id === providerId && roleCfg.model === model) {
      nextRoles[roleId] = { ...roleCfg, provider_id: undefined, model: undefined };
      rolesChanged = true;
    }
  });

  return {
    ...config,
    providers: {
      ...config.providers,
      [providerId]: providerCfg,
    },
    roles: rolesChanged ? nextRoles : config.roles,
  };
};

export const removeProvider = (
  config: VisualGraphConfig,
  providerId: string
): VisualGraphConfig => {
  // Remove provider
  const nextProviders = { ...(config.providers || {}) };
  delete nextProviders[providerId];
  
  // Clear roles using this provider
  const nextRoles = { ...(config.roles || {}) };
  let rolesChanged = false;
  Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
    if (roleCfg.provider_id === providerId) {
      nextRoles[roleId] = { ...roleCfg, provider_id: undefined, model: undefined };
      rolesChanged = true;
    }
  });

  return {
    ...config,
    providers: nextProviders,
    roles: rolesChanged ? nextRoles : config.roles,
  };
};



export const extractNodeStates = (nodes: Node<VisualNodeData>[], edges: Edge<VisualEdgeData>[]): Record<string, VisualNodeState> => {
  const states: Record<string, VisualNodeState> = {};
  
  nodes.forEach((node) => {
    const state: VisualNodeState = {
      position: node.position ? { x: node.position.x, y: node.position.y } : undefined,
      selected: node.selected || false,
      hidden: node.hidden || false,
    };
    
    // 根据节点类型提取特定状态
    if (node.type === 'role' && node.data.kind === 'role') {
      state.data = {
        roleData: {
          readinessScore: node.data.readiness?.grade ? parseFloat(node.data.readiness.grade) : undefined,
        }
      };
    } else if (node.type === 'model' && node.data.kind === 'model') {
      state.data = {
        modelData: {
          assignedRoles: node.data.assignedRoles,
        }
      };
    }
    
    states[node.id] = state;
  });
  
  return states;
};

export const restoreNodeStates = (
  nodes: Node<VisualNodeData>[], 
  savedStates: Record<string, VisualNodeState>
): Node<VisualNodeData>[] => {
  const resolveSavedState = (node: Node<VisualNodeData>): VisualNodeState | undefined => {
    const direct = savedStates[node.id];
    if (direct) return direct;
    if (node.type === 'provider' && node.data.kind === 'provider') {
      return savedStates[legacyProviderNodeId(node.data.providerId)];
    }
    if (node.type === 'model' && node.data.kind === 'model') {
      return savedStates[legacyModelNodeId(node.data.providerId, node.data.model)];
    }
    return undefined;
  };

  return nodes.map((node) => {
    const savedState = resolveSavedState(node);
    if (!savedState) return node;
    
    const updatedNode = { ...node };
    
    // 恢复节点数据状态（不包括位置）
    if (savedState.data) {
      updatedNode.data = { ...updatedNode.data };
      
      if (node.type === 'role' && savedState.data.roleData) {
        (updatedNode.data as VisualRoleNodeData).readiness = savedState.data.roleData.readinessScore 
          ? { ready: savedState.data.roleData.readinessScore > 0.5, grade: savedState.data.roleData.readinessScore.toString() }
          : undefined;
      } else if (node.type === 'provider' && savedState.data.providerData) {
        // Provider connectivity state is dynamic and must be sourced from runtime/list status.
      } else if (node.type === 'model' && savedState.data.modelData) {
        (updatedNode.data as VisualModelNodeData).assignedRoles = savedState.data.modelData.assignedRoles as VisualRoleId[];
      }
    }
    
    // 恢复选中状态
    if (savedState.selected !== undefined) {
      updatedNode.selected = savedState.selected;
    }
    
    // 恢复隐藏状态
    if (savedState.hidden !== undefined) {
      updatedNode.hidden = savedState.hidden;
    }
    
    // 位置恢复将在mergeNodePositions中处理，这里不处理
    return updatedNode;
  });
};

export const extractNodePositions = (nodes: Node<VisualNodeData>[]): Record<string, VisualNodePosition> => {
  const layout: Record<string, VisualNodePosition> = {};
  nodes.forEach((node) => {
    if (node.position) {
      layout[node.id] = { x: node.position.x, y: node.position.y };
    }
  });
  return layout;
};

export const updateVisualLayout = (
  config: VisualGraphConfig,
  nodes: Node<VisualNodeData>[]
): VisualGraphConfig => {
  const layout = extractNodePositions(nodes);
  return {
    ...config,
    visual_layout: layout,
  };
};

export const updateVisualStates = (
  config: VisualGraphConfig,
  nodes: Node<VisualNodeData>[],
  edges: Edge<VisualEdgeData>[],
  viewport?: { x: number; y: number; zoom: number }
): VisualGraphConfig => {
  const states = extractNodeStates(nodes, edges);
  return {
    ...config,
    visual_node_states: states,
    visual_viewport: viewport || config.visual_viewport,
  };
};

// ============================================================================
// Runtime Configuration Conversion
// ============================================================================

export interface RoleAssignment {
  roleId: VisualRoleId;
  providerId: string;
  model: string;
  profile?: string;
}

export interface RuntimeLLMConfig {
  providers: Record<string, unknown>;
  roleAssignments: RoleAssignment[];
  version: string;
  generatedAt: string;
}

/**
 * Convert VisualGraphConfig to runtime configuration format
 * This ensures the visual configuration can be consumed by backend runtime scripts
 */
export const visualToRuntimeConfig = (config: VisualGraphConfig): RuntimeLLMConfig => {
  const roleAssignments: RoleAssignment[] = [];

  Object.entries(config.roles || {}).forEach(([roleId, roleCfg]) => {
    if (roleCfg?.provider_id && roleCfg?.model) {
      roleAssignments.push({
        roleId: normalizeVisualRoleId(roleId) || 'architect',
        providerId: roleCfg.provider_id,
        model: roleCfg.model,
        profile: roleCfg.profile || 'default',
      });
    }
  });

  return {
    providers: config.providers,
    roleAssignments,
    version: '1.0',
    generatedAt: new Date().toISOString(),
  };
};

/**
 * Check if all required roles have valid model assignments
 */
export const validateRoleAssignments = (
  config: VisualGraphConfig
): { valid: boolean; missing: VisualRoleId[]; incomplete: VisualRoleId[] } => {
  const requiredRoles: VisualRoleId[] = ['pm', 'director', 'qa', 'architect'];
  const missing: VisualRoleId[] = [];
  const incomplete: VisualRoleId[] = [];

  requiredRoles.forEach((roleId) => {
    const roleCfg = config.roles?.[roleId] || (roleId === 'architect' ? config.roles?.docs : undefined);
    if (!roleCfg) {
      missing.push(roleId);
    } else if (!roleCfg.provider_id || !roleCfg.model) {
      incomplete.push(roleId);
    }
  });

  return {
    valid: missing.length === 0 && incomplete.length === 0,
    missing,
    incomplete,
  };
};

/**
 * Get human-readable configuration summary
 */
export const getConfigSummary = (config: VisualGraphConfig): string => {
  const assignments: string[] = [];

  const roleOrder: VisualRoleId[] = ['pm', 'director', 'qa', 'architect'];
  roleOrder.forEach((roleId) => {
    const roleCfg = config.roles?.[roleId] || (roleId === 'architect' ? config.roles?.docs : undefined);
    if (roleCfg?.provider_id && roleCfg?.model) {
      assignments.push(`${roleId}: ${roleCfg.provider_id}/${roleCfg.model}`);
    } else {
      assignments.push(`${roleId}: [未配置]`);
    }
  });

  return assignments.join('\n');
};
