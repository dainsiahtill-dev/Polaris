import { ViewAdapter } from './types';
import type { 
  UnifiedLlmConfig, 
  UnifiedProvider, 
  UnifiedRole, 
  ProviderConfig,
  CostClass,
  ProviderCategory
} from '../types';

export interface ProviderListItem {
  id: string;
  name: string;
  type: string;
  status: 'unknown' | 'success' | 'failed' | 'testing';
  costClass: CostClass;
  lastTest?: string;
  category: ProviderCategory;
  config: ProviderConfig;
}

export interface RoleListItem {
  id: string;
  name: string;
  description: string;
  assignment?: {
    providerId: string;
    model: string;
    confidence: number;
  };
  readiness: 'unknown' | 'ready' | 'not_ready';
}

export interface AssignmentItem {
  roleId: string;
  providerId: string;
  model: string;
  confidence?: number;
}

export interface ListViewData {
  providers: ProviderListItem[];
  roles: RoleListItem[];
  assignments: AssignmentItem[];
}

export interface ListViewState {
  selectedProviderId?: string;
  selectedRoleId?: string;
  editingProviderId?: string;
  filterText?: string;
}

export class ListViewAdapter implements ViewAdapter<ListViewData, ListViewState> {
  
  createViewState(): ListViewState {
    return {};
  }

  adaptToView(unifiedData: UnifiedLlmConfig): ListViewData {
    return {
      providers: Object.values(unifiedData.providers).map(provider => ({
        id: provider.id,
        name: provider.name,
        type: provider.type,
        status: provider.attributes.connectivity_status,
        costClass: provider.attributes.cost_class,
        lastTest: provider.attributes.last_test_timestamp,
        category: provider.attributes.provider_category,
        config: provider.config
      })),
      roles: Object.values(unifiedData.roles).map(role => ({
        id: role.id,
        name: role.name,
        description: role.description,
        assignment: role.assignment ? {
          providerId: role.assignment.provider_id,
          model: role.assignment.model,
          confidence: role.assignment.confidence
        } : undefined,
        readiness: role.attributes.readiness_status
      })),
      assignments: Object.entries(unifiedData.relationships.role_to_provider_model).map(
        ([roleId, assignment]) => ({
          roleId,
          providerId: assignment.provider_id,
          model: assignment.model,
          confidence: assignment.confidence
        })
      )
    };
  }

  adaptFromView(viewData: ListViewData, unifiedData: UnifiedLlmConfig): Partial<UnifiedLlmConfig> {
    const updates: Partial<UnifiedLlmConfig> = {
      providers: { ...unifiedData.providers },
      roles: { ...unifiedData.roles },
      relationships: { ...unifiedData.relationships }
    };

    // Update Providers
    // Note: We only update existing providers or add new ones based on the list.
    // Deletions would need simpler handling (e.g., if it's missing from list, remove it? 
    // Or maybe list view operations are more granular than full replace).
    // For this generic 'adaptFromView', we assume viewData is the target state.
    
    // However, usually specific actions updates specific parts. 
    // If we use this for full sync:
    viewData.providers.forEach(item => {
      if (updates.providers![item.id]) {
        updates.providers![item.id] = {
          ...updates.providers![item.id],
          name: item.name,
          config: item.config,
          // We probably don't update status from list view directly unless it's an edit
        };
      } else {
        // New provider (simplified creation logic, really should be done via specific action)
        // Here we just acknowledge it exists in view data
      }
    });

    // Update Assignments in Relationships
    updates.relationships!.role_to_provider_model = {};
    viewData.assignments.forEach(assignment => {
      updates.relationships!.role_to_provider_model[assignment.roleId] = {
        provider_id: assignment.providerId,
        model: assignment.model,
        confidence: assignment.confidence
      };
      
      // Also update the role object itself to reflect assignment
      if (updates.roles![assignment.roleId]) {
        updates.roles![assignment.roleId] = {
          ...updates.roles![assignment.roleId],
          assignment: {
            provider_id: assignment.providerId,
            model: assignment.model,
            assigned_at: new Date().toISOString(), // Update timestamp on change
            confidence: assignment.confidence || 0.8
          }
        };
      }
    });

    return updates;
  }
}
