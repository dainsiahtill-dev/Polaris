import type { ProviderConfig, RoleConfig } from '../types';

type LlmConfigWithBindings = {
  providers?: Record<string, ProviderConfig>;
  roles?: Record<string, RoleConfig>;
};

function hasProvider(providers: Record<string, ProviderConfig>, providerId: string): boolean {
  return Object.prototype.hasOwnProperty.call(providers, providerId);
}

export function sanitizeLlmConfigForSave<T extends LlmConfigWithBindings>(config: T): T {
  const providers = config.providers && typeof config.providers === 'object' ? config.providers : {};
  const roles = config.roles && typeof config.roles === 'object' ? config.roles : {};
  let changed = false;
  const nextRoles: Record<string, RoleConfig> = {};

  Object.entries(roles).forEach(([roleId, roleCfg]) => {
    const nextRoleCfg: RoleConfig = { ...(roleCfg || {}) };
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
    } else if (nextRoleCfg.model !== model) {
      nextRoleCfg.model = model;
      changed = true;
    }

    nextRoles[roleId] = nextRoleCfg;
  });

  return changed ? { ...config, roles: nextRoles } : config;
}
