import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import {
  type ProviderInfo,
  type ProviderConfig,
  requiresApiKeyForType,
  type ValidationResult,
  type ProviderSettingsProps
} from './types';

export interface ProviderRegistryEntry {
  info: ProviderInfo;
  defaultConfig: ProviderConfig;
  component: React.ComponentType<ProviderSettingsProps>;
}

class ProviderRegistryClass {
  private providers: Map<string, ProviderRegistryEntry> = new Map();
  private loading: boolean = false;
  private error: string | null = null;

  async loadProviders(): Promise<void> {
    if (this.loading) return;
    
    this.loading = true;
    this.error = null;
    
    try {
      // Fetch providers from backend
      const response = await apiFetch('/llm/providers');
      if (!response.ok) {
        throw new Error(`加载提供商失败: ${response.statusText}`);
      }
      
      const data = await response.json();
      const providers: ProviderInfo[] = data.providers || [];
      
      // Load each provider's details
      for (const providerInfo of providers) {
        try {
          const [defaultConfig, component] = await Promise.all([
            this.fetchProviderConfig(providerInfo.type),
            this.loadProviderComponent(providerInfo.type)
          ]);
          
          this.providers.set(providerInfo.type, {
            info: providerInfo,
            defaultConfig,
            component
          });
        } catch (err) {
          devLogger.warn(`加载提供商详情失败 ${providerInfo.type}:`, err);
        }
      }
    } catch (err) {
      this.error = err instanceof Error ? err.message : '未知错误';
      devLogger.error('加载提供商失败:', err);
    } finally {
      this.loading = false;
    }
  }

  private async fetchProviderConfig(providerType: string): Promise<ProviderConfig> {
    const response = await apiFetch(`/llm/providers/${providerType}/config`);
    if (!response.ok) {
      throw new Error(`获取配置失败： ${providerType}`);
    }
    return response.json();
  }

  private async loadProviderComponent(providerType: string): Promise<React.ComponentType<ProviderSettingsProps>> {
    // Dynamic import of provider component
    switch (providerType) {
      case 'codex_cli':
        const { CodexCLIProviderSettings } = await import('./providers/CodexCLIProviderSettings');
        return CodexCLIProviderSettings;
      case 'codex_sdk':
        const { CodexSDKProviderSettings } = await import('./providers/CodexSDKProviderSettings');
        return CodexSDKProviderSettings;
      case 'gemini_cli':
        const { GeminiCLIProviderSettings } = await import('./providers/GeminiCLIProviderSettings');
        return GeminiCLIProviderSettings;
      case 'minimax':
        const { MiniMaxProviderSettings } = await import('./providers/MiniMaxProviderSettings');
        return MiniMaxProviderSettings;
      case 'kimi':
        const { KimiProviderSettings } = await import('./providers/KimiProviderSettings');
        return KimiProviderSettings;
      case 'gemini_api':
        const { GeminiAPIProviderSettings } = await import('./providers/GeminiAPIProviderSettings');
        return GeminiAPIProviderSettings;
      case 'ollama':
        const { OllamaProviderSettings } = await import('./providers/OllamaProviderSettings');
        return OllamaProviderSettings;
      case 'openai_compat':
        const { OpenAICompatProviderSettings } = await import('./providers/OpenAICompatProviderSettings');
        return OpenAICompatProviderSettings;
      case 'anthropic_compat':
        const { AnthropicCompatProviderSettings } = await import('./providers/AnthropicCompatProviderSettings');
        return AnthropicCompatProviderSettings;
      default:
        const { DefaultProviderSettings } = await import('./providers/DefaultProviderSettings');
        return DefaultProviderSettings;
    }
  }

  getProviderTypes(): string[] {
    return Array.from(this.providers.keys());
  }

  getProviderInfo(providerType: string): ProviderInfo | undefined {
    return this.providers.get(providerType)?.info;
  }

  getProviderDefaultConfig(providerType: string): ProviderConfig | undefined {
    return this.providers.get(providerType)?.defaultConfig;
  }

  getProviderComponent(providerType: string): React.ComponentType<ProviderSettingsProps> | undefined {
    return this.providers.get(providerType)?.component;
  }

  getAllProviders(): ProviderRegistryEntry[] {
    return Array.from(this.providers.values());
  }

  requiresApiKey(providerType: string): boolean {
    const providerInfo = this.getProviderInfo(providerType);
    if (!providerInfo) return true; // Default to requiring API key
    return requiresApiKeyForType(providerType);
  }

  supportsFeature(providerType: string, feature: string): boolean {
    const providerInfo = this.getProviderInfo(providerType);
    return providerInfo?.supported_features.includes(feature) || false;
  }

  getCostClass(providerType: string): 'LOCAL' | 'FIXED' | 'METERED' {
    const providerInfo = this.getProviderInfo(providerType);
    return providerInfo?.cost_class || 'METERED';
  }

  isLoading(): boolean {
    return this.loading;
  }

  getError(): string | null {
    return this.error;
  }

  async validateProviderConfig(providerType: string, config: ProviderConfig): Promise<ValidationResult> {
    try {
      const response = await apiFetch(`/llm/providers/${providerType}/validate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(config),
      });
      
      if (!response.ok) {
        throw new Error(`校验失败: ${response.statusText}`);
      }
      
      return response.json();
    } catch (err) {
      return {
        valid: false,
        errors: [err instanceof Error ? err.message : '校验失败'],
        warnings: [],
      };
    }
  }

}

// Global instance
export const ProviderRegistry = new ProviderRegistryClass();

// Hook for using the provider registry
export function useProviderRegistry() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadProviders = async () => {
      try {
        await ProviderRegistry.loadProviders();
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载提供商失败');
      } finally {
        setLoading(false);
      }
    };

    loadProviders();
  }, []);

  return {
    loading: loading || ProviderRegistry.isLoading(),
    error: error || ProviderRegistry.getError(),
    providers: ProviderRegistry.getAllProviders(),
    getProviderInfo: ProviderRegistry.getProviderInfo.bind(ProviderRegistry),
    getProviderDefaultConfig: ProviderRegistry.getProviderDefaultConfig.bind(ProviderRegistry),
    getProviderComponent: ProviderRegistry.getProviderComponent.bind(ProviderRegistry),
    requiresApiKey: ProviderRegistry.requiresApiKey.bind(ProviderRegistry),
    supportsFeature: ProviderRegistry.supportsFeature.bind(ProviderRegistry),
    getCostClass: ProviderRegistry.getCostClass.bind(ProviderRegistry),
    validateProviderConfig: ProviderRegistry.validateProviderConfig.bind(ProviderRegistry),
  };
}
