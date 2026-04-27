import type { Connection, Node } from '@xyflow/react';
import type { VisualGraphConfig, ValidationIssue, VisualNodeData, VisualRoleId } from '../types/visual';

export const isValidVisualConnection = (
  connection: Connection,
  nodes: Node<VisualNodeData>[]
): boolean => {
  if (!connection.source || !connection.target) return false;
  const source = nodes.find((node) => node.id === connection.source);
  const target = nodes.find((node) => node.id === connection.target);
  if (!source || !target) return false;

  if (source.type === 'model' && target.type === 'role') {
    return true;
  }

  if (source.type === 'provider' && target.type === 'model') {
    const sourceData = source.data;
    const targetData = target.data;
    const sourceProvider = sourceData.kind === 'provider' ? sourceData.providerId : undefined;
    const targetProvider = targetData.kind === 'model' ? targetData.providerId : undefined;
    return Boolean(sourceProvider && targetProvider && sourceProvider === targetProvider);
  }

  // Allow Provider -> Role (will be auto-routed to a model)
  if (source.type === 'provider' && target.type === 'role') {
    return true;
  }

  return false;
};

// ============================================================================
// Enhanced Validation
// ============================================================================

export const validateVisualGraph = (
  config: VisualGraphConfig
): { valid: boolean; issues: ValidationIssue[] } => {
  const issues: ValidationIssue[] = [];

  // Check each role has valid configuration
  const roleIds: VisualRoleId[] = [
    'pm',
    'director',
    'chief_engineer',
    'qa',
    'architect',
    'cfo',
    'hr',
  ];

  roleIds.forEach((roleId) => {
    const roleCfg = config.roles?.[roleId];

    if (!roleCfg?.provider_id) {
      issues.push({
        type: 'DISCONNECTED_ROLE',
        nodeId: `role:${roleId}`,
        message: `角色 ${getRoleLabel(roleId)} 未连接到提供商`,
        suggestion: '请从提供商拖拽连线到该角色',
      });
    } else if (!roleCfg?.model) {
      issues.push({
        type: 'MISSING_MODEL',
        nodeId: `role:${roleId}`,
        message: `角色 ${getRoleLabel(roleId)} 未配置模型`,
        suggestion: '请为该角色选择一个模型',
      });
    }
  });

  // Check if Provider exists
  Object.entries(config.roles || {}).forEach(([roleId, roleCfg]) => {
    if (roleCfg?.provider_id) {
      const provider = config.providers?.[roleCfg.provider_id];
      if (!provider) {
        issues.push({
          type: 'INVALID_PROVIDER',
          nodeId: `role:${roleId}`,
          message: `角色 ${getRoleLabel(roleId as VisualRoleId)} 配置的提供商不存在`,
          suggestion: '请重新配置提供商',
        });
      }
    }
  });

  return {
    valid: issues.length === 0,
    issues,
  };
};

export const getRoleLabel = (roleId: VisualRoleId | string): string => {
  const labels: Record<string, string> = {
    pm: 'PM',
    director: 'Director',
    chief_engineer: 'Chief Engineer',
    qa: 'QA',
    architect: 'Architect',
    cfo: 'CFO',
    hr: 'HR',
    docs: 'Architect',
  };
  return labels[roleId] || roleId;
};

export const getValidationSeverity = (issue: ValidationIssue): 'error' | 'warning' => {
  switch (issue.type) {
    case 'MISSING_MODEL':
    case 'INVALID_PROVIDER':
      return 'error';
    case 'DISCONNECTED_ROLE':
    case 'MODEL_NOT_FOUND':
      return 'warning';
    default:
      return 'warning';
  }
};
