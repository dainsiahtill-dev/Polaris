export type VisualRoleId =
  | 'pm'           // PM
  | 'director'     // Director (coding)
  | 'chief_engineer' // Chief Engineer (design)
  | 'qa'           // QA
  | 'architect'    // Architect
  | 'cfo'          // CFO
  | 'hr';          // HR

export type VisualNodeKind = 'role' | 'provider' | 'model';

export interface VisualRoleNodeData extends Record<string, unknown> {
  kind: 'role';
  roleId: VisualRoleId;
  label: string;
  description?: string;
  requiresThinking?: boolean;
  minConfidence?: number;
  readiness?: {
    ready?: boolean;
    grade?: string;
  };
  runtimeStatus?: {
    running: boolean;
    startedAt?: string;
    lastRun?: string;
    lastStatus?: string;
    config: {
      provider_id?: string;
      model?: string;
    };
  };
}

export interface VisualProviderNodeData extends Record<string, unknown> {
  kind: 'provider';
  providerId: string;
  label: string;
  providerType?: string;
  costClass?: string;
  status?: string;
  modelCount?: number;
}

export interface VisualModelNodeData extends Record<string, unknown> {
  kind: 'model';
  providerId: string;
  model: string;
  label: string;
  assignedRoles?: VisualRoleId[];
}

export type VisualNodeData = VisualRoleNodeData | VisualProviderNodeData | VisualModelNodeData;

export type VisualEdgeKind = 'provider-to-model' | 'model-to-role';

export interface VisualEdgeData extends Record<string, unknown> {
  kind: VisualEdgeKind;
}

export interface VisualNodePosition {
  x: number;
  y: number;
}

export interface VisualNodeState {
  // 节点位置
  position?: VisualNodePosition;
  // 节点展开/折叠状态
  expanded?: boolean;
  // 节点选中状态
  selected?: boolean;
  // 节点可见性
  hidden?: boolean;
  // 节点数据状态
  data?: {
    // 角色节点的特定状态
    roleData?: {
      lastInterviewStatus?: 'passed' | 'failed' | 'none';
      lastInterviewTimestamp?: string;
      readinessScore?: number;
    };
    // Provider节点的特定状态
    providerData?: {
      connectivityStatus?: 'success' | 'failed' | 'unknown';
      lastTestTimestamp?: string;
      enabledModels?: string[];
    };
    // Model节点的特定状态
    modelData?: {
      assignedRoles?: string[];
      lastUsedTimestamp?: string;
      performanceScore?: number;
    };
  };
}

export interface VisualGraphConfig {
  providers: Record<string, unknown>;
  roles: Record<string, { provider_id?: string; model?: string; profile?: string }>;
  visual_layout?: Record<string, VisualNodePosition>;
  // 新增：完整的节点状态持久化
  visual_node_states?: Record<string, VisualNodeState>;
  // 视图状态
  visual_viewport?: {
    x: number;
    y: number;
    zoom: number;
  };
  policies?: {
    role_requirements?: Record<string, { requires_thinking?: boolean; min_confidence?: number; error_message?: string }>;
  };
}

export interface VisualGraphStatus {
  roles?: Record<string, { ready?: boolean; grade?: string } | undefined>;
  providers?: Record<
    string,
    { status?: 'unknown' | 'running' | 'success' | 'failed' } | undefined
  >;
}

// ============================================================================
// Runtime Configuration Types
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

// ============================================================================
// Validation Types
// ============================================================================

export type ValidationIssueType = 
  | 'MISSING_MODEL' 
  | 'INVALID_PROVIDER' 
  | 'DISCONNECTED_ROLE' 
  | 'MODEL_NOT_FOUND';

export interface ValidationIssue {
  type: ValidationIssueType;
  nodeId: string;
  message: string;
  suggestion?: string;
}
