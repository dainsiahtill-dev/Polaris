import type { NodeTypes, EdgeTypes } from '@xyflow/react';
import { VisualRoleNode } from '../nodes/VisualRoleNode';
import { VisualProviderNode } from '../nodes/VisualProviderNode';
import { VisualModelNode } from '../nodes/VisualModelNode';
import { CustomEdge } from '../edges/CustomEdge';

export const nodeTypes: NodeTypes = {
  role: VisualRoleNode,
  provider: VisualProviderNode,
  model: VisualModelNode,
};

export const edgeTypes: EdgeTypes = {
  custom: CustomEdge,
};
