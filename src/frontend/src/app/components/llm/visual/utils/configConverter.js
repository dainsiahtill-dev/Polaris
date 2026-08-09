import { getLlmRoleDefinition, getRequiredLlmAssignmentRoleIds, getVisibleLlmBindingRoleIds, normalizeLlmRoleId, } from "../../roleDefinitions";
const encodeNodeSegment = (value) => encodeURIComponent(value);
const legacyNormalizeId = (value) => value.replace(/[^a-zA-Z0-9_-]/g, "_");
const legacyProviderNodeId = (providerId) => `provider:${legacyNormalizeId(providerId)}`;
const legacyModelNodeId = (providerId, model) => `model:${legacyNormalizeId(providerId)}:${legacyNormalizeId(model)}`;
export const roleNodeId = (roleId) => `role:${roleId}`;
const normalizeVisualRoleId = (roleId) => {
    return normalizeLlmRoleId(roleId);
};
export const providerNodeId = (providerId) => `provider:${encodeNodeSegment(providerId)}`;
export const modelNodeId = (providerId, model) => `model:${encodeNodeSegment(providerId)}:${encodeNodeSegment(model)}`;
const coerceManualModels = (config) => {
    const manual = config.manual_models;
    if (Array.isArray(manual)) {
        return manual.map((item) => String(item)).filter(Boolean);
    }
    return [];
};
const parseOptionalPositiveInt = (value) => {
    if (value === undefined || value === null || value === "")
        return undefined;
    const parsed = Number.parseInt(String(value), 10);
    if (!Number.isFinite(parsed) || parsed <= 0)
        return undefined;
    return parsed;
};
export const getRoleBindings = (roleCfg) => {
    if (!roleCfg)
        return [];
    if (Array.isArray(roleCfg.bindings)) {
        return roleCfg.bindings
            .map((binding) => {
            const providerId = String(binding?.provider_id || "").trim();
            const model = String(binding?.model || "").trim();
            if (!providerId || !model)
                return null;
            const next = {
                provider_id: providerId,
                model,
            };
            const profile = String(binding.profile || "").trim();
            if (profile)
                next.profile = profile;
            const maxConcurrency = parseOptionalPositiveInt(binding.max_concurrency ?? binding.concurrency);
            if (maxConcurrency !== undefined)
                next.max_concurrency = maxConcurrency;
            return next;
        })
            .filter((binding) => Boolean(binding));
    }
    const providerId = String(roleCfg.provider_id || "").trim();
    const model = String(roleCfg.model || "").trim();
    if (!providerId || !model)
        return [];
    const binding = { provider_id: providerId, model };
    const profile = String(roleCfg.profile || "").trim();
    if (profile)
        binding.profile = profile;
    return [binding];
};
const mirrorPrimaryBinding = (roleCfg) => {
    const bindings = getRoleBindings(roleCfg);
    const nextRole = { ...roleCfg, bindings };
    const primary = bindings[0];
    if (!primary) {
        delete nextRole.provider_id;
        delete nextRole.model;
        return nextRole;
    }
    nextRole.provider_id = primary.provider_id;
    nextRole.model = primary.model;
    if (primary.profile) {
        nextRole.profile = primary.profile;
    }
    return nextRole;
};
export const buildVisualGraph = (config, status) => {
    const providers = Object.entries(config.providers || {});
    const roleReqs = config.policies?.role_requirements || {};
    const savedLayout = config.visual_layout || {};
    // Helper to safely restore position
    const restorePosition = (nodeId, defaultPosition, fallbackNodeIds = []) => {
        const ids = [nodeId, ...fallbackNodeIds];
        for (const id of ids) {
            const saved = savedLayout[id];
            if (saved && typeof saved.x === "number" && typeof saved.y === "number") {
                return saved;
            }
        }
        return defaultPosition;
    };
    const providerModels = new Map();
    const addModel = (providerId, model) => {
        if (!providerId || !model)
            return;
        if (!providerModels.has(providerId)) {
            providerModels.set(providerId, new Set());
        }
        providerModels.get(providerId)?.add(model);
    };
    Object.entries(config.roles || {}).forEach(([, roleCfg]) => {
        getRoleBindings(roleCfg).forEach((binding) => {
            addModel(binding.provider_id, binding.model);
        });
    });
    providers.forEach(([providerId, providerCfgRaw]) => {
        const providerCfg = typeof providerCfgRaw === "object" && providerCfgRaw !== null
            ? providerCfgRaw
            : {};
        const configuredModels = [providerCfg.default_model, providerCfg.model]
            .filter((item) => typeof item === "string")
            .map((item) => item.trim())
            .filter(Boolean);
        configuredModels.forEach((model) => addModel(providerId, model));
        const manualModels = coerceManualModels(providerCfg);
        manualModels.forEach((model) => addModel(providerId, model));
    });
    const nodes = [];
    const edges = [];
    providers.forEach(([providerId, providerCfgRaw], providerIndex) => {
        const providerCfg = typeof providerCfgRaw === "object" && providerCfgRaw !== null
            ? providerCfgRaw
            : {};
        const providerType = typeof providerCfg.type === "string" ? providerCfg.type : undefined;
        const providerLabel = typeof providerCfg.name === "string" && providerCfg.name.trim()
            ? providerCfg.name.trim()
            : providerId;
        const rawProviderStatus = status?.providers?.[providerId]?.status;
        const providerStatus = rawProviderStatus === "running" ||
            rawProviderStatus === "success" ||
            rawProviderStatus === "failed" ||
            rawProviderStatus === "unknown"
            ? rawProviderStatus
            : "unknown";
        const modelList = Array.from(providerModels.get(providerId) || []);
        const providerIdValue = providerNodeId(providerId);
        const providerNode = {
            id: providerIdValue,
            type: "provider",
            position: restorePosition(providerIdValue, { x: 40, y: providerIndex * 180 + 40 }, [legacyProviderNodeId(providerId)]),
            data: {
                kind: "provider",
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
            const modelNode = {
                id: modelId,
                type: "model",
                position: restorePosition(modelId, { x: 340, y: providerIndex * 180 + modelIndex * 120 + 40 }, [legacyModelNodeId(providerId, model)]),
                data: {
                    kind: "model",
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
                type: "custom",
                data: { kind: "provider-to-model" },
            });
        });
    });
    const requiredRoleIds = getRequiredLlmAssignmentRoleIds(config.policies);
    const visibleRoleIds = [
        ...new Set([
            ...getVisibleLlmBindingRoleIds(config.roles, status?.roles),
            ...requiredRoleIds,
        ]),
    ];
    visibleRoleIds.forEach((roleId, index) => {
        const requirement = roleReqs[roleId] || {};
        const readiness = status?.roles?.[roleId];
        const meta = getLlmRoleDefinition(roleId);
        nodes.push({
            id: roleNodeId(roleId),
            type: "role",
            position: restorePosition(roleNodeId(roleId), {
                x: 700,
                y: index * 180 + 40,
            }),
            data: {
                kind: "role",
                roleId,
                label: meta.label,
                description: meta.description,
                requiresThinking: Boolean(requirement.requires_thinking),
                minConfidence: typeof requirement.min_confidence === "number"
                    ? requirement.min_confidence
                    : undefined,
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
        const roleIdNormalized = normalizeVisualRoleId(roleId);
        if (!roleIdNormalized)
            return;
        getRoleBindings(roleCfg).forEach((binding, bindingIndex) => {
            const modelId = modelNodeId(binding.provider_id, binding.model);
            const modelNode = nodes.find((node) => node.id === modelId);
            if (modelNode && modelNode.type === "model") {
                const data = modelNode.data;
                data.assignedRoles = Array.from(new Set([...(data.assignedRoles || []), roleIdNormalized]));
            }
            edges.push({
                id: `edge:${modelId}:${roleNodeId(roleIdNormalized)}:${bindingIndex}`,
                source: modelId,
                target: roleNodeId(roleIdNormalized),
                type: "custom",
                data: {
                    kind: "model-to-role",
                    roleId: roleIdNormalized,
                    providerId: binding.provider_id,
                    model: binding.model,
                    bindingIndex,
                    maxConcurrency: binding.max_concurrency,
                },
            });
        });
    });
    return { nodes, edges };
};
export const mergeNodePositions = (previous, next) => {
    const positions = new Map(previous.map((node) => [node.id, node.position]));
    return next.map((node) => {
        const position = positions.get(node.id);
        return position ? { ...node, position } : node;
    });
};
export const mergeNodePositionsWithStates = (previous, next, savedStates) => {
    // Keep current in-memory node position first to avoid drag-reset during async refresh.
    const previousPositions = new Map(previous.map((node) => [node.id, node.position]));
    const resolveSavedState = (node) => {
        const direct = savedStates[node.id];
        if (direct)
            return direct;
        if (node.type === "provider" && node.data.kind === "provider") {
            return savedStates[legacyProviderNodeId(node.data.providerId)];
        }
        if (node.type === "model" && node.data.kind === "model") {
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
export const updateRoleAssignment = (config, roleId, providerId, model, options = {}) => {
    const currentRole = config.roles?.[roleId] || {};
    const bindings = getRoleBindings(currentRole);
    const normalizedProviderId = providerId.trim();
    const normalizedModel = model.trim();
    if (!normalizedProviderId || !normalizedModel)
        return config;
    const existingIndex = bindings.findIndex((binding) => binding.provider_id === normalizedProviderId &&
        binding.model === normalizedModel);
    const nextBinding = {
        provider_id: normalizedProviderId,
        model: normalizedModel,
    };
    const profile = String(options.profile || "").trim();
    if (profile)
        nextBinding.profile = profile;
    const maxConcurrency = parseOptionalPositiveInt(options.maxConcurrency);
    if (maxConcurrency !== undefined)
        nextBinding.max_concurrency = maxConcurrency;
    const nextBindings = [...bindings];
    if (existingIndex >= 0) {
        nextBindings[existingIndex] = {
            ...nextBindings[existingIndex],
            ...nextBinding,
        };
    }
    else {
        nextBindings.push(nextBinding);
    }
    const nextRole = mirrorPrimaryBinding({
        ...currentRole,
        bindings: nextBindings,
    });
    return {
        ...config,
        roles: {
            ...config.roles,
            [roleId]: nextRole,
        },
    };
};
export const clearRoleAssignment = (config, roleId) => {
    const nextRole = { ...(config.roles?.[roleId] || {}) };
    delete nextRole.provider_id;
    delete nextRole.model;
    nextRole.bindings = [];
    return {
        ...config,
        roles: {
            ...config.roles,
            [roleId]: nextRole,
        },
    };
};
export const removeRoleBinding = (config, roleId, providerId, model) => {
    const currentRole = config.roles?.[roleId] || {};
    const nextBindings = getRoleBindings(currentRole).filter((binding) => !(binding.provider_id === providerId && binding.model === model));
    const nextRole = mirrorPrimaryBinding({
        ...currentRole,
        bindings: nextBindings,
    });
    return {
        ...config,
        roles: {
            ...config.roles,
            [roleId]: nextRole,
        },
    };
};
export const addManualModel = (config, providerId, model) => {
    const raw = config.providers?.[providerId];
    const providerCfg = typeof raw === "object" && raw !== null
        ? { ...raw }
        : {};
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
export const removeManualModel = (config, providerId, model) => {
    const raw = config.providers?.[providerId];
    if (!raw || typeof raw !== "object")
        return config;
    const providerCfg = { ...raw };
    const manualModels = coerceManualModels(providerCfg);
    const nextManual = manualModels.filter((m) => m !== model);
    providerCfg.manual_models = nextManual;
    // Clear roles using this model
    const nextRoles = { ...(config.roles || {}) };
    let rolesChanged = false;
    Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
        const bindings = getRoleBindings(roleCfg);
        const nextBindings = bindings.filter((binding) => !(binding.provider_id === providerId && binding.model === model));
        if (nextBindings.length !== bindings.length) {
            nextRoles[roleId] = mirrorPrimaryBinding({
                ...roleCfg,
                bindings: nextBindings,
            });
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
export const removeProvider = (config, providerId) => {
    // Remove provider
    const nextProviders = { ...(config.providers || {}) };
    delete nextProviders[providerId];
    // Clear roles using this provider
    const nextRoles = { ...(config.roles || {}) };
    let rolesChanged = false;
    Object.entries(nextRoles).forEach(([roleId, roleCfg]) => {
        const bindings = getRoleBindings(roleCfg);
        const nextBindings = bindings.filter((binding) => binding.provider_id !== providerId);
        if (nextBindings.length !== bindings.length) {
            nextRoles[roleId] = mirrorPrimaryBinding({
                ...roleCfg,
                bindings: nextBindings,
            });
            rolesChanged = true;
        }
    });
    return {
        ...config,
        providers: nextProviders,
        roles: rolesChanged ? nextRoles : config.roles,
    };
};
export const extractNodeStates = (nodes, edges) => {
    const states = {};
    nodes.forEach((node) => {
        const state = {
            position: node.position
                ? { x: node.position.x, y: node.position.y }
                : undefined,
            selected: node.selected || false,
            hidden: node.hidden || false,
        };
        // 根据节点类型提取特定状态
        if (node.type === "role" && node.data.kind === "role") {
            state.data = {
                roleData: {
                    readinessScore: node.data.readiness?.grade
                        ? parseFloat(node.data.readiness.grade)
                        : undefined,
                },
            };
        }
        else if (node.type === "model" && node.data.kind === "model") {
            state.data = {
                modelData: {
                    assignedRoles: node.data.assignedRoles,
                },
            };
        }
        states[node.id] = state;
    });
    return states;
};
export const restoreNodeStates = (nodes, savedStates) => {
    const resolveSavedState = (node) => {
        const direct = savedStates[node.id];
        if (direct)
            return direct;
        if (node.type === "provider" && node.data.kind === "provider") {
            return savedStates[legacyProviderNodeId(node.data.providerId)];
        }
        if (node.type === "model" && node.data.kind === "model") {
            return savedStates[legacyModelNodeId(node.data.providerId, node.data.model)];
        }
        return undefined;
    };
    return nodes.map((node) => {
        const savedState = resolveSavedState(node);
        if (!savedState)
            return node;
        const updatedNode = { ...node };
        // 恢复节点数据状态（不包括位置）
        if (savedState.data) {
            updatedNode.data = { ...updatedNode.data };
            if (node.type === "role" && savedState.data.roleData) {
                updatedNode.data.readiness = savedState.data
                    .roleData.readinessScore
                    ? {
                        ready: savedState.data.roleData.readinessScore > 0.5,
                        grade: savedState.data.roleData.readinessScore.toString(),
                    }
                    : undefined;
            }
            else if (node.type === "provider" && savedState.data.providerData) {
                // Provider connectivity state is dynamic and must be sourced from runtime/list status.
            }
            else if (node.type === "model" && savedState.data.modelData) {
                updatedNode.data.assignedRoles = savedState
                    .data.modelData.assignedRoles;
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
export const extractNodePositions = (nodes) => {
    const layout = {};
    nodes.forEach((node) => {
        if (node.position) {
            layout[node.id] = { x: node.position.x, y: node.position.y };
        }
    });
    return layout;
};
export const updateVisualLayout = (config, nodes) => {
    const layout = extractNodePositions(nodes);
    return {
        ...config,
        visual_layout: layout,
    };
};
export const updateVisualStates = (config, nodes, edges, viewport) => {
    const states = extractNodeStates(nodes, edges);
    return {
        ...config,
        visual_node_states: states,
        visual_viewport: viewport || config.visual_viewport,
    };
};
/**
 * Convert VisualGraphConfig to runtime configuration format
 * This ensures the visual configuration can be consumed by backend runtime scripts
 */
export const visualToRuntimeConfig = (config) => {
    const roleAssignments = [];
    Object.entries(config.roles || {}).forEach(([roleId, roleCfg]) => {
        const normalizedRoleId = normalizeVisualRoleId(roleId) || "architect";
        const roleMaxConcurrency = parseOptionalPositiveInt(roleCfg?.max_concurrency ?? roleCfg?.concurrency);
        getRoleBindings(roleCfg).forEach((binding) => {
            const assignment = {
                roleId: normalizedRoleId,
                providerId: binding.provider_id,
                model: binding.model,
                profile: binding.profile || roleCfg.profile || "default",
            };
            const maxConcurrency = parseOptionalPositiveInt(binding.max_concurrency ?? binding.concurrency);
            if (maxConcurrency !== undefined)
                assignment.maxConcurrency = maxConcurrency;
            if (roleMaxConcurrency !== undefined)
                assignment.roleMaxConcurrency = roleMaxConcurrency;
            roleAssignments.push(assignment);
        });
        if (!getRoleBindings(roleCfg).length &&
            roleCfg?.provider_id &&
            roleCfg?.model) {
            roleAssignments.push({
                roleId: normalizedRoleId,
                providerId: roleCfg.provider_id,
                model: roleCfg.model,
                profile: roleCfg.profile || "default",
            });
        }
    });
    return {
        providers: config.providers,
        roleAssignments,
        version: "1.0",
        generatedAt: new Date().toISOString(),
    };
};
/**
 * Check if all required roles have valid model assignments
 */
export const validateRoleAssignments = (config) => {
    const requiredRoles = getRequiredLlmAssignmentRoleIds(config.policies);
    const missing = [];
    const incomplete = [];
    requiredRoles.forEach((roleId) => {
        const roleCfg = config.roles?.[roleId] ||
            (roleId === "architect" ? config.roles?.docs : undefined);
        const bindings = getRoleBindings(roleCfg);
        if (!roleCfg) {
            missing.push(roleId);
        }
        else if (!bindings.length && (!roleCfg.provider_id || !roleCfg.model)) {
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
export const getConfigSummary = (config) => {
    const assignments = [];
    const roleOrder = getRequiredLlmAssignmentRoleIds(config.policies);
    roleOrder.forEach((roleId) => {
        const roleCfg = config.roles?.[roleId] ||
            (roleId === "architect" ? config.roles?.docs : undefined);
        const bindings = getRoleBindings(roleCfg);
        if (bindings.length) {
            assignments.push(`${roleId}: ${bindings.map((binding) => `${binding.provider_id}/${binding.model}`).join(", ")}`);
        }
        else {
            assignments.push(`${roleId}: [未配置]`);
        }
    });
    return assignments.join("\n");
};
export const updateProviderConcurrency = (config, providerId, value) => {
    const raw = config.providers?.[providerId];
    const providerCfg = typeof raw === "object" && raw !== null
        ? { ...raw }
        : {};
    const nextValue = parseOptionalPositiveInt(value);
    if (nextValue === undefined) {
        delete providerCfg.max_concurrency;
    }
    else {
        providerCfg.max_concurrency = nextValue;
    }
    return {
        ...config,
        providers: {
            ...config.providers,
            [providerId]: providerCfg,
        },
    };
};
export const updateRoleConcurrency = (config, roleId, value) => {
    const nextRole = { ...(config.roles?.[roleId] || {}) };
    const nextValue = parseOptionalPositiveInt(value);
    if (nextValue === undefined) {
        delete nextRole.max_concurrency;
    }
    else {
        nextRole.max_concurrency = nextValue;
    }
    return {
        ...config,
        roles: {
            ...config.roles,
            [roleId]: nextRole,
        },
    };
};
export const updateRoleBindingConcurrency = (config, roleId, providerId, model, value) => {
    const currentRole = config.roles?.[roleId] || {};
    const nextValue = parseOptionalPositiveInt(value);
    const nextBindings = getRoleBindings(currentRole).map((binding) => {
        if (binding.provider_id !== providerId || binding.model !== model) {
            return binding;
        }
        const nextBinding = { ...binding };
        if (nextValue === undefined) {
            delete nextBinding.max_concurrency;
        }
        else {
            nextBinding.max_concurrency = nextValue;
        }
        return nextBinding;
    });
    return {
        ...config,
        roles: {
            ...config.roles,
            [roleId]: mirrorPrimaryBinding({
                ...currentRole,
                bindings: nextBindings,
            }),
        },
    };
};
