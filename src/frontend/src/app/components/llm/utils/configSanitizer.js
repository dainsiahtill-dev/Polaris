function hasProvider(providers, providerId) {
    return Object.prototype.hasOwnProperty.call(providers, providerId);
}
export function sanitizeLlmConfigForSave(config) {
    const providers = config.providers && typeof config.providers === 'object' ? config.providers : {};
    const roles = config.roles && typeof config.roles === 'object' ? config.roles : {};
    let changed = false;
    const nextRoles = {};
    Object.entries(roles).forEach(([roleId, roleCfg]) => {
        const nextRoleCfg = { ...(roleCfg || {}) };
        const providerId = typeof nextRoleCfg.provider_id === 'string' ? nextRoleCfg.provider_id.trim() : '';
        if (!providerId || !hasProvider(providers, providerId)) {
            if ('provider_id' in nextRoleCfg || 'model' in nextRoleCfg) {
                changed = true;
            }
            delete nextRoleCfg.provider_id;
            delete nextRoleCfg.model;
            nextRoles[roleId] = nextRoleCfg;
            return;
        }
        if (nextRoleCfg.provider_id !== providerId) {
            nextRoleCfg.provider_id = providerId;
            changed = true;
        }
        const model = typeof nextRoleCfg.model === 'string' ? nextRoleCfg.model.trim() : '';
        if (!model) {
            if ('model' in nextRoleCfg) {
                changed = true;
            }
            delete nextRoleCfg.model;
        }
        else if (nextRoleCfg.model !== model) {
            nextRoleCfg.model = model;
            changed = true;
        }
        nextRoles[roleId] = nextRoleCfg;
    });
    return changed ? { ...config, roles: nextRoles } : config;
}
