export function resolveProviderConfiguredModel(providerCfg) {
    if (!providerCfg)
        return '';
    if (typeof providerCfg.model === 'string' && providerCfg.model.trim())
        return providerCfg.model.trim();
    if (typeof providerCfg.model_id === 'string' && providerCfg.model_id.trim())
        return providerCfg.model_id.trim();
    if (typeof providerCfg.default_model === 'string' && providerCfg.default_model.trim())
        return providerCfg.default_model.trim();
    return '';
}
export function resolveProviderAwareRoleModel(roleCfg, providerId, providerCfg, overrideModel) {
    if (typeof overrideModel === 'string' && overrideModel.trim()) {
        return overrideModel.trim();
    }
    const roleProviderId = typeof roleCfg?.provider_id === 'string' ? roleCfg.provider_id.trim() : '';
    const roleModel = typeof roleCfg?.model === 'string' ? roleCfg.model.trim() : '';
    if (roleModel && roleProviderId && roleProviderId === providerId) {
        return roleModel;
    }
    return resolveProviderConfiguredModel(providerCfg);
}
