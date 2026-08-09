import { VisualRoleNode } from '../nodes/VisualRoleNode';
import { VisualProviderNode } from '../nodes/VisualProviderNode';
import { VisualModelNode } from '../nodes/VisualModelNode';
import { CustomEdge } from '../edges/CustomEdge';
export const nodeTypes = {
    role: VisualRoleNode,
    provider: VisualProviderNode,
    model: VisualModelNode,
};
export const edgeTypes = {
    custom: CustomEdge,
};
