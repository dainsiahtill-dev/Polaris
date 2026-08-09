/**
 * StrictViewAdapter
 *
 * Phase 4.2: Type Safety Enhancement
 * - Strict type constraints for view adapters
 * - Type-safe update operations
 * - Discriminated unions for operation types
 */
export class StrictListViewAdapter {
    adaptToView(unifiedData) {
        const providers = Object.entries(unifiedData.providers || {}).map(([id, config]) => ({
            id,
            name: config.name || id,
            type: config.type || 'unknown',
            status: 'unknown',
        }));
        const roles = Object.entries(unifiedData.roles || {}).map(([id, config]) => ({
            id,
            name: id,
            assignment: config.assignment,
        }));
        const assignments = Object.entries(unifiedData.roles || {})
            .filter(([, config]) => config.assignment)
            .map(([roleId, config]) => ({
            roleId,
            providerId: config.assignment.provider_id,
            model: config.assignment.model || 'default',
        }));
        return { providers, roles, assignments };
    }
    adaptFromView(viewData, unifiedData) {
        // Convert view data back to unified format
        const providers = {};
        viewData.providers.forEach((p) => {
            providers[p.id] = {
                ...unifiedData.providers?.[p.id],
                name: p.name,
                type: p.type,
            };
        });
        const roles = {};
        viewData.roles.forEach((r) => {
            if (r.assignment) {
                roles[r.id] = {
                    ...unifiedData.roles?.[r.id],
                    assignment: {
                        provider_id: r.assignment.provider_id,
                        model: r.assignment.model,
                        assigned_at: new Date().toISOString(),
                        confidence: 0.8,
                    },
                };
            }
        });
        return { providers, roles };
    }
    createViewState() {
        return {
            selectedProviderId: null,
            selectedRoleId: null,
            filter: '',
            sortBy: 'name',
            sortOrder: 'asc',
            expandedProviders: new Set(),
        };
    }
    updateViewState(state, changes) {
        return { ...state, ...changes };
    }
    getSupportedOperations() {
        return [
            'update_provider',
            'update_role',
            'update_assignment',
            'add_provider',
            'remove_provider',
            'reorder_providers',
        ];
    }
    isOperationSupported(operation) {
        return this.getSupportedOperations().includes(operation);
    }
    validateOperation(operation, params) {
        const errors = [];
        switch (operation) {
            case 'update_provider':
                if (!isListPayloadWithProviderId(params)) {
                    errors.push('providerId is required');
                }
                else if (!params.config) {
                    errors.push('config is required');
                }
                break;
            case 'update_role':
                if (!isListPayloadWithRoleId(params))
                    errors.push('roleId is required');
                break;
            case 'update_assignment':
                if (!isListPayloadWithAssignment(params)) {
                    errors.push('roleId is required');
                    errors.push('providerId is required');
                }
                break;
            case 'add_provider':
                if (!isListPayloadWithProviderId(params)) {
                    errors.push('providerId is required');
                }
                else if (!params.config) {
                    errors.push('config is required');
                }
                break;
            case 'remove_provider':
                if (!isListPayloadWithProviderId(params))
                    errors.push('providerId is required');
                break;
            case 'reorder_providers':
                if (!isListPayloadWithOrder(params) || !Array.isArray(params.order)) {
                    errors.push('order must be an array');
                }
                break;
        }
        return { valid: errors.length === 0, errors };
    }
    executeOperation(operation, params) {
        // Validate first
        const validation = this.validateOperation(operation, params);
        if (!validation.valid) {
            throw new Error(`Invalid operation: ${validation.errors.join(', ')}`);
        }
        switch (operation) {
            case 'update_provider':
                if (isListPayloadWithProviderId(params)) {
                    const p = {
                        providers: {
                            [params.providerId]: params.config,
                        },
                    };
                    return p;
                }
                break;
            case 'update_role':
                if (isListPayloadWithRoleId(params)) {
                    const r = {
                        roles: {
                            [params.roleId]: params.config,
                        },
                    };
                    return r;
                }
                break;
            case 'update_assignment':
                if (isListPayloadWithAssignment(params)) {
                    const a = {
                        roles: {
                            [params.roleId]: {
                                assignment: {
                                    provider_id: params.providerId,
                                    model: params.model,
                                    assigned_at: new Date().toISOString(),
                                    confidence: 0.8,
                                },
                            },
                        },
                    };
                    return a;
                }
                break;
            case 'add_provider':
                if (isListPayloadWithProviderId(params)) {
                    const p = {
                        providers: {
                            [params.providerId]: params.config,
                        },
                    };
                    return p;
                }
                break;
            case 'remove_provider':
                if (isListPayloadWithProviderId(params)) {
                    const r = {
                        providers: {
                            [params.providerId]: undefined,
                        },
                    };
                    return r;
                }
                break;
            case 'reorder_providers':
                // Reordering doesn't change unified data, only view state
                return {};
            default:
                throw new Error(`Unsupported operation: ${operation}`);
        }
        return {};
    }
}
// ============================================================================
// Type Guard Functions
// ============================================================================
// Type guards for operation params
function isListPayloadWithProviderId(params) {
    return typeof params === 'object' && params !== null && 'providerId' in params;
}
function isListPayloadWithRoleId(params) {
    return typeof params === 'object' && params !== null && 'roleId' in params;
}
function isListPayloadWithOrder(params) {
    return typeof params === 'object' && params !== null && 'order' in params;
}
function isListPayloadWithAssignment(params) {
    return (typeof params === 'object' &&
        params !== null &&
        'roleId' in params &&
        'providerId' in params &&
        'model' in params);
}
export function isListOperation(operation) {
    return operation.viewType === 'list';
}
export function isVisualOperation(operation) {
    return operation.viewType === 'visual';
}
export function isTestOperation(operation) {
    return operation.viewType === 'deepTest';
}
// ============================================================================
// Operation Factory Functions
// ============================================================================
export const ListOperations = {
    updateProvider: (providerId, config) => ({
        viewType: 'list',
        type: 'update_provider',
        payload: { providerId, config },
    }),
    updateRole: (roleId, config) => ({
        viewType: 'list',
        type: 'update_role',
        payload: { roleId, config },
    }),
    updateAssignment: (roleId, providerId, model) => ({
        viewType: 'list',
        type: 'update_assignment',
        payload: { roleId, providerId, model },
    }),
    addProvider: (providerId, config) => ({
        viewType: 'list',
        type: 'add_provider',
        payload: { providerId, config },
    }),
    removeProvider: (providerId) => ({
        viewType: 'list',
        type: 'remove_provider',
        payload: { providerId },
    }),
    reorderProviders: (order) => ({
        viewType: 'list',
        type: 'reorder_providers',
        payload: { order },
    }),
};
// ============================================================================
// Type-safe Operation Executor
// ============================================================================
export class TypedOperationExecutor {
    constructor(adapter) {
        this.adapter = adapter;
    }
    execute(operation) {
        if (!this.adapter.isOperationSupported(operation.type)) {
            throw new Error(`Operation ${operation.type} is not supported by this adapter`);
        }
        return this.adapter.executeOperation(operation.type, operation.payload);
    }
    validate(operation) {
        if (!this.adapter.isOperationSupported(operation.type)) {
            return { valid: false, errors: [`Operation ${operation.type} is not supported`] };
        }
        return this.adapter.validateOperation(operation.type, operation.payload);
    }
}
