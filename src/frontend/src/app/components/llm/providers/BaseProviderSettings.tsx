import React, { useState, useEffect } from 'react';
import { AlertTriangle, CheckCircle2, Info } from 'lucide-react';
import {
  type ProviderConfig,
  type ValidationResult,
  isCLIProviderType,
  requiresApiKeyForType,
  usesBaseUrlForType
} from '../types';
import { cyberInputClassesAlt } from '@/app/components/ui/cyber-input-classes';

interface BaseProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: () => ValidationResult;
  children?: React.ReactNode;
  hideApiKey?: boolean;
  hideBaseUrl?: boolean;
}

// Cyberpunk style input classes - using alt variant with semi-transparent background
const cyberInputClasses = cyberInputClassesAlt;

const parseOptionalPositiveInt = (value: string): number | undefined => {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  const parsed = Number.parseInt(trimmed, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }
  return parsed;
};

export function BaseProviderSettings({ 
  provider, 
  onUpdate, 
  onValidate, 
  children,
  hideApiKey,
  hideBaseUrl
}: BaseProviderSettingsProps) {
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const providerNameValue = provider.name == null ? '' : String(provider.name);
  const contextWindowValue =
    typeof provider.max_context_tokens === 'number'
      ? provider.max_context_tokens
      : typeof provider.context_window === 'number'
        ? provider.context_window
        : '';
  const maxOutputValue =
    typeof provider.max_output_tokens === 'number'
      ? provider.max_output_tokens
      : typeof provider.max_tokens === 'number'
        ? provider.max_tokens
        : '';

  useEffect(() => {
    const result = onValidate();
    setValidationResult(result);
  }, [provider, onValidate]);

  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  const renderValidationStatus = () => {
    if (!validationResult) return null;

    if (validationResult.valid) {
      return (
        <div className="flex items-center gap-2 text-emerald-400 text-xs">
          <CheckCircle2 className="size-3" />
          <span>配置校验通过</span>
        </div>
      );
    }

    return (
      <div className="space-y-1">
        {validationResult.errors.map((error, index) => (
          <div key={index} className="flex items-center gap-2 text-red-400 text-xs">
            <AlertTriangle className="size-3" />
            <span>{error}</span>
          </div>
        ))}
        {validationResult.warnings.map((warning, index) => (
          <div key={index} className="flex items-center gap-2 text-yellow-400 text-xs">
            <Info className="size-3" />
            <span>{warning}</span>
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Basic Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">基础配置</h5>
        
        {/* Provider Name */}
        <div>
          <label className="block text-xs text-text-muted mb-1">提供商名称</label>
          <input
            type="text"
            value={providerNameValue}
            onChange={(e) => handleFieldChange('name', e.target.value)}
            className={cyberInputClasses}
            placeholder="我的 LLM 提供商"
          />
        </div>

        {/* API Key - Only show if provider requires it and not hidden */}
        {provider.type && requiresApiKeyForType(provider.type) && !hideApiKey && (
          <div>
            <label className="block text-xs text-text-muted mb-1">API 密钥</label>
            <input
              type="text"
              value={provider.api_key || ''}
              onChange={(e) => handleFieldChange('api_key', e.target.value)}
              className={`${cyberInputClasses} font-mono`}
              placeholder="请输入 API 密钥"
            />
            <p className="text-[9px] text-text-dim mt-1">
              API 密钥将保存并用于鉴权
            </p>
          </div>
        )}

        {/* Base URL - For API providers */}
        {provider.type && usesBaseUrlForType(provider.type) && !hideBaseUrl && (
          <div>
            <label className="block text-xs text-text-muted mb-1">基础 URL</label>
            <input
              type="text"
              value={provider.base_url || ''}
              onChange={(e) => handleFieldChange('base_url', e.target.value)}
              className={`${cyberInputClasses} font-mono`}
              placeholder="https://api.example.com/v1"
            />
          </div>
        )}

        {/* Command - For CLI providers */}
        {provider.type && isCLIProviderType(provider.type) && (
          <div>
            <label className="block text-xs text-text-muted mb-1">命令</label>
            <input
              type="text"
              value={provider.command || ''}
              onChange={(e) => handleFieldChange('command', e.target.value)}
              className={`${cyberInputClasses} font-mono`}
              placeholder="例如 codex、gemini"
            />
          </div>
        )}

        {/* Timeout */}
        <div>
          <label className="block text-xs text-text-muted mb-1">超时（秒）</label>
          <input
            type="number"
            value={provider.timeout || 60}
            onChange={(e) => handleFieldChange('timeout', parseInt(e.target.value) || 60)}
            className={cyberInputClasses}
            min="1"
            max="300"
          />
        </div>

        <div>
          <label className="block text-xs text-text-muted mb-1">上下文窗口大小（Context Window Size）</label>
          <input
            type="number"
            data-testid="provider-max-context-tokens-input"
            value={contextWindowValue}
            onChange={(e) => handleFieldChange('max_context_tokens', parseOptionalPositiveInt(e.target.value))}
            className={cyberInputClasses}
            min="1"
            step="1"
            placeholder="例如 200000"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于 Token Budget 与上下文压缩预算计算。
          </p>
        </div>

        <div>
          <label className="block text-xs text-text-muted mb-1">最大输出 Tokens（Max Output Tokens）</label>
          <input
            type="number"
            data-testid="provider-max-output-tokens-input"
            value={maxOutputValue}
            onChange={(e) => handleFieldChange('max_output_tokens', parseOptionalPositiveInt(e.target.value))}
            className={cyberInputClasses}
            min="1"
            step="1"
            placeholder="例如 8192"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于控制保留输出预算，避免上下文挤占回复空间。
          </p>
        </div>
      </div>

      {/* Provider-specific settings */}
      {children}

      {/* Validation Status */}
      <div className="pt-3 border-t border-white/10">
        {renderValidationStatus()}
      </div>
    </div>
  );
}
