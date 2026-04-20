/**
 * Unified Model Resolver
 * 解决模型字段为空的问题，提供健壮的 fallback 机制
 */

import type { ProviderConfig } from '../types';
import type { ProviderInfo } from '../types';

export interface ModelResolutionContext {
  roleId?: string;
  providerId?: string;
  llmConfig?: {
    roles?: Record<string, { provider_id?: string; model?: string } | null>;
    providers?: Record<string, ProviderConfig | null>;
  } | null;
  providers?: Array<ProviderInfo & { id: string; model?: string }>;
}

export interface ModelResolutionResult {
  model: string;
  source: 'role_config' | 'provider_config' | 'provider_default' | 'hardcoded_fallback';
  isValid: boolean;
  warning?: string;
}

export interface ModelValidationResult {
  isValid: boolean;
  error?: string;
}

function normalizeRoleId(roleId?: string): string | undefined {
  if (!roleId) return undefined;
  const normalized = roleId.trim().toLowerCase();
  if (normalized === 'docs') return 'architect';
  return normalized;
}

const MODEL_FALLBACKS: Record<string, string> = {
  'openai': 'gpt-4',
  'openai_compat': 'gpt-4',
  'anthropic': 'claude-3-sonnet-20240229',
  'anthropic_compat': 'claude-3-sonnet-20240229',
  'kimi': 'kimi-k2-thinking-turbo',
  'minimax': 'abab6.5-chat',
  'gemini_api': 'gemini-1.5-pro',
  'ollama': 'llama2',
  'codex_cli': 'gpt-4-codex',
  'codex_sdk': 'gpt-4',
  'gemini_cli': 'gemini-1.5-pro',
  'custom_https': 'gpt-4',
};

export function resolveModelName(context: ModelResolutionContext): ModelResolutionResult {
  const { roleId, providerId, llmConfig, providers } = context;

  const normalizedRoleId = normalizeRoleId(roleId);
  const roleConfig = normalizedRoleId
    ? (llmConfig?.roles?.[normalizedRoleId] || (normalizedRoleId === 'architect' ? llmConfig?.roles?.docs : null))
    : null;
  if (roleConfig?.model) {
    const roleModel = roleConfig.model;
    const roleProviderId =
      typeof roleConfig.provider_id === 'string' ? roleConfig.provider_id.trim() : '';
    const providerMatchesRole =
      !providerId || !roleProviderId || roleProviderId === providerId;
    if (providerMatchesRole && roleModel && roleModel.trim()) {
      return {
        model: roleModel.trim(),
        source: 'role_config',
        isValid: true
      };
    }
  }

  if (providerId && llmConfig?.providers?.[providerId]) {
    const providerConfig = llmConfig.providers[providerId];
    if (!providerConfig) {
      // Provider config is null
    } else {
      const model = providerConfig.model;
      if (typeof model === 'string' && model.trim()) {
        return {
          model: model.trim(),
          source: 'provider_config',
          isValid: true
        };
      }

      const modelId = providerConfig.model_id;
      if (typeof modelId === 'string' && modelId.trim()) {
        return {
          model: modelId.trim(),
          source: 'provider_config',
          isValid: true
        };
      }

      const defaultModel = providerConfig.default_model;
      if (typeof defaultModel === 'string' && defaultModel.trim()) {
        return {
          model: defaultModel.trim(),
          source: 'provider_default',
          isValid: true
        };
      }
    }
  }

  if (providerId && providers) {
    const provider = providers.find(p => p.id === providerId);
    if (provider?.model && provider.model.trim()) {
      return {
        model: provider.model.trim(),
        source: 'provider_config',
        isValid: true
      };
    }

    const providerType = provider?.type || '';
    const fallbackModel = MODEL_FALLBACKS[providerType];
    if (fallbackModel) {
      return {
        model: fallbackModel,
        source: 'hardcoded_fallback',
        isValid: true,
        warning: `使用默认模型 ${fallbackModel}，建议在配置中明确指定模型`
      };
    }
  }

  return {
    model: 'gpt-4',
    source: 'hardcoded_fallback',
    isValid: false,
    warning: '无法确定模型，使用通用 fallback 模型'
  };
}

export function validateModelName(model: string, _providerType?: string): ModelValidationResult {
  if (!model || typeof model !== 'string' || model.trim() === '') {
    return {
      isValid: false,
      error: '模型名称不能为空'
    };
  }

  if (/[<>"'&]/.test(model)) {
    return {
      isValid: false,
      error: '模型名称包含无效字符'
    };
  }

  if (model.length > 100) {
    return {
      isValid: false,
      error: '模型名称过长'
    };
  }

  return { isValid: true };
}

export function getModelResolutionLog(context: ModelResolutionContext): string {
  const result = resolveModelName(context);

  return `模型解析结果:
- 角色: ${context.roleId || '未指定'}
- Provider: ${context.providerId || '未指定'}
- 解析模型: ${result.model}
- 来源: ${result.source}
- 有效性: ${result.isValid ? '有效' : '无效'}
- 警告: ${result.warning || '无'}`;
}

export function getDefaultModelForProvider(providerType: string): string | null {
  return MODEL_FALLBACKS[providerType] || null;
}
