import { useState, useEffect } from 'react';
import { apiFetch } from '@/api';
import { devLogger } from '@/app/utils/devLogger';
import { requiresApiKeyForType } from './types';
class ProviderRegistryClass {
    constructor() {
        this.providers = new Map();
        this.loading = false;
        this.error = null;
    }
    async loadProviders() {
        if (this.loading)
            return;
        this.loading = true;
        this.error = null;
        try {
            // Fetch providers from backend
            const response = await apiFetch('/v2/llm/providers');
            if (!response.ok) {
                throw new Error(`加载提供商失败: ${response.statusText}`);
            }
            const data = await response.json();
            const providers = data.providers || [];
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
                }
                catch (err) {
                    devLogger.warn(`加载提供商详情失败 ${providerInfo.type}:`, err);
                }
            }
        }
        catch (err) {
            this.error = err instanceof Error ? err.message : '未知错误';
            devLogger.error('加载提供商失败:', err);
        }
        finally {
            this.loading = false;
        }
    }
    async fetchProviderConfig(providerType) {
        const response = await apiFetch(`/v2/llm/providers/${providerType}/config`);
        if (!response.ok) {
            throw new Error(`获取配置失败： ${providerType}`);
        }
        return response.json();
    }
    async loadProviderComponent(providerType) {
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
                const { OpenAIProviderSettings } = await import('./providers/OpenAIProviderSettings');
                return OpenAIProviderSettings;
            case 'anthropic_compat':
                const { AnthropicProviderSettings } = await import('./providers/AnthropicProviderSettings');
                return AnthropicProviderSettings;
            default:
                const { DefaultProviderSettings } = await import('./providers/DefaultProviderSettings');
                return DefaultProviderSettings;
        }
    }
    getProviderTypes() {
        return Array.from(this.providers.keys());
    }
    getProviderInfo(providerType) {
        return this.providers.get(providerType)?.info;
    }
    getProviderDefaultConfig(providerType) {
        return this.providers.get(providerType)?.defaultConfig;
    }
    getProviderComponent(providerType) {
        return this.providers.get(providerType)?.component;
    }
    getAllProviders() {
        return Array.from(this.providers.values());
    }
    requiresApiKey(providerType) {
        const providerInfo = this.getProviderInfo(providerType);
        if (!providerInfo)
            return true; // Default to requiring API key
        return requiresApiKeyForType(providerType);
    }
    supportsFeature(providerType, feature) {
        const providerInfo = this.getProviderInfo(providerType);
        return providerInfo?.supported_features.includes(feature) || false;
    }
    getCostClass(providerType) {
        const providerInfo = this.getProviderInfo(providerType);
        return providerInfo?.cost_class || 'METERED';
    }
    isLoading() {
        return this.loading;
    }
    getError() {
        return this.error;
    }
    async validateProviderConfig(providerType, config) {
        try {
            const response = await apiFetch(`/v2/llm/providers/${providerType}/validate`, {
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
        }
        catch (err) {
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
    const [error, setError] = useState(null);
    useEffect(() => {
        const loadProviders = async () => {
            try {
                await ProviderRegistry.loadProviders();
                setError(null);
            }
            catch (err) {
                setError(err instanceof Error ? err.message : '加载提供商失败');
            }
            finally {
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
