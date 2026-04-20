
import { ViewAdapter } from './types';
import type { 
  UnifiedLlmConfig, 
  UnifiedProvider, 
  UnifiedRole,
  Position,
  NodeStyle,
  ViewportState
} from '../types';

// Visual Data Structures (Specific to React Flow or similar lib)
export interface VisualNode {
  id: string;
  type: 'provider' | 'role';
  position: Position;
  data: {
    label: string;
    details?: {
      requires_thinking: boolean;
      min_confidence: number;
      preferred_capabilities: string[];
    };
    status?: string;
    style?: NodeStyle;
  };
  // React Flow specific fields could be added here
}

export interface VisualEdge {
  id: string;
  source: string;
  target: string;
  data?: {
    label?: string;
    style?: NodeStyle;
  };
}

export interface VisualViewData {
  nodes: VisualNode[];
  edges: VisualEdge[];
  viewport: ViewportState;
}

export interface VisualViewState {
  selectedNodes: Set<string>;
  expandedNodes: Set<string>;
  draggedNode?: string;
  layoutAlgorithm: 'manual' | 'hierarchical' | 'force';
}

export class VisualViewAdapter implements ViewAdapter<VisualViewData, VisualViewState> {
  createViewState(): VisualViewState {
    return {
      selectedNodes: new Set(),
      expandedNodes: new Set(),
      layoutAlgorithm: 'manual'
    };
  }

  adaptToView(unifiedData: UnifiedLlmConfig): VisualViewData {
    const nodes: VisualNode[] = [];
    const edges: VisualEdge[] = [];

    // Providers -> Nodes
    Object.values(unifiedData.providers).forEach(provider => {
      nodes.push({
        id: provider.id, // ID collision check? Provider IDs are unique strings usually
        type: 'provider',
        position: provider.attributes.visual?.position || { x: 0, y: 0 },
        data: {
          label: provider.name,
          status: provider.attributes.connectivity_status,
          style: provider.attributes.visual?.style
        }
      });
    });

    // Roles -> Nodes
    Object.values(unifiedData.roles).forEach(role => {
      nodes.push({
        id: role.id,
        type: 'role',
        position: role.attributes.visual?.position || { x: 0, y: 0 },
        data: {
          label: role.name,
          details: role.requirements,
          status: role.attributes.readiness_status,
          style: role.attributes.visual?.style
        }
      });
    });

    // Assignments -> Edges
    Object.entries(unifiedData.relationships.role_to_provider_model).forEach(([roleId, assignment]) => {
      edges.push({
        id: `edge-${roleId}-${assignment.provider_id}`,
        source: roleId,
        target: assignment.provider_id,
        data: {
          label: assignment.model,
          style: unifiedData.extensions.visual?.edge_metadata?.[`edge-${roleId}-${assignment.provider_id}`]
        }
      });
    });

    return {
      nodes,
      edges,
      viewport: unifiedData.extensions.visual?.viewport_state || { x: 0, y: 0, zoom: 1 }
    };
  }

  adaptFromView(viewData: VisualViewData, unifiedData: UnifiedLlmConfig): Partial<UnifiedLlmConfig> {
    const updates: Partial<UnifiedLlmConfig> = {
      providers: { ...unifiedData.providers },
      roles: { ...unifiedData.roles },
      extensions: {
        ...unifiedData.extensions,
        visual: {
          ...unifiedData.extensions.visual,
          node_positions: {}, // Will be rebuilt
          node_styles: unifiedData.extensions.visual?.node_styles || {},
          edge_metadata: {},
          viewport_state: viewData.viewport
        }
      } as Partial<UnifiedLlmConfig>['extensions']
    };

    // Update positions
    viewData.nodes.forEach(node => {
      if (node.type === 'provider') {
        if (updates.providers![node.id]) {
          updates.providers![node.id] = {
            ...updates.providers![node.id],
            attributes: {
              ...updates.providers![node.id].attributes,
              visual: {
                ...updates.providers![node.id].attributes.visual,
                position: node.position
              }
            }
          };
        }
      } else if (node.type === 'role') {
        if (updates.roles![node.id]) {
          updates.roles![node.id] = {
            ...updates.roles![node.id],
            attributes: {
              ...updates.roles![node.id].attributes,
              visual: {
                ...updates.roles![node.id].attributes.visual,
                position: node.position
              }
            }
          };
        }
      }
      
      // Also update centralized position map if needed
      if (updates.extensions?.visual?.node_positions) {
        updates.extensions.visual.node_positions[node.id] = node.position;
      }
    });

    return updates;
  }
}
