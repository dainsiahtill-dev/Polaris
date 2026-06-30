import React, { useEffect, useState } from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';

import { cyberInputClasses, cyberTextareaCompactClasses } from '@/app/components/ui/cyber-input-classes';

const cyberTextareaClasses = cyberTextareaCompactClasses;

interface AnthropicProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

const parseCustomHeadersInput = (rawValue: string): Record<string, string> | null => {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return {};
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const normalized: Record<string, string> = {};
      Object.entries(parsed as Record<string, unknown>).forEach(([key, value]) => {
        if (!key || value === undefined || value === null) return;
        normalized[String(key)] = String(value);
      });
      return normalized;
    }
  } catch {
    // Fall through to line-based parsing.
  }

  // Prevent partially typed JSON from being misread as line-based headers.
  if (trimmed.includes('{') || trimmed.includes('}')) {
    return null;
  }

  const lines = trimmed
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return {};
  }

  const parsedHeaders: Record<string, string> = {};
  for (const line of lines) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex <= 0) {
      return null;
    }
    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim();
    if (!key) {
      return null;
    }
    parsedHeaders[key] = value;
  }

  return parsedHeaders;
};

export function AnthropicProviderSettings({
  provider,
  onUpdate,
  onValidate
}: AnthropicProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };
  const serializedHeaders = JSON.stringify(provider.headers || {}, null, 2);
  const [headersText, setHeadersText] = useState(serializedHeaders);
  const anthropicVersion =
    typeof provider.anthropic_version === 'string' && provider.anthropic_version.trim() !== ''
      ? provider.anthropic_version
      : '2023-06-01';
  const modelId =
    typeof provider.model === 'string' && provider.model.trim() !== ''
      ? provider.model
      : typeof provider.default_model === 'string'
        ? provider.default_model
        : '';

  useEffect(() => {
    setHeadersText(serializedHeaders);
  }, [serializedHeaders]);

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      {/* Anthropic Compatible Specific Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Anthropic 兼容配置</h5>
        
        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <input
            type="text"
            value={provider.api_path || '/v1/messages'}
            onChange={(e) => handleFieldChange('api_path', e.target.value)}
            placeholder="/v1/messages"
            className={cyberInputClasses}
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于连通性测试的 Messages 接口地址
          </p>
        </div>

        {/* API Version */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 版本</label>
          <input
            type="text"
            value={anthropicVersion}
            onChange={(e) => handleFieldChange('anthropic_version', e.target.value)}
            placeholder="2023-06-01"
            className={cyberInputClasses}
          />
        </div>

        {/* Custom Headers */}
        <div>
          <label className="block text-xs text-text-muted mb-1">自定义请求头（JSON）</label>
          <textarea
            data-testid="anthropic-custom-headers-input"
            value={headersText}
            onChange={(e) => {
              const nextValue = e.target.value;
              setHeadersText(nextValue);

              const parsedHeaders = parseCustomHeadersInput(nextValue);
              if (parsedHeaders) {
                handleFieldChange('headers', parsedHeaders);
              }
            }}
            className={cyberTextareaClasses}
            placeholder='{"anthropic-version": "2023-06-01"}'
          />
          <p className="text-[9px] text-text-dim mt-1">
            支持 JSON，或按行填写 `Key: Value`。
          </p>
        </div>
      </div>

      {/* Model Configuration */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">模型配置</h5>
        <div>
          <label className="block text-xs text-text-muted mb-1">模型 ID</label>
          <input
            type="text"
            data-testid="anthropic-model-id-input"
            value={modelId}
            onChange={(e) => handleFieldChange('model', e.target.value)}
            placeholder="请输入兼容模型 ID"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">
            支持 Anthropic 兼容服务的第三方模型 ID。
          </p>
        </div>
      </div>

      {/* Advanced Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">高级参数</h5>
        
        {/* Temperature */}
        <div>
          <label className="block text-xs text-text-muted mb-1">温度（Temperature）</label>
          <input
            type="number"
            value={provider.temperature ?? 0.2}
            onChange={(e) => {
              // ?? + NaN-guard (NOT `|| default`): 0 is falsy, so `0 || 0.2`
              // silently forced 0.2 and blocked setting temperature to 0.
              const parsed = parseFloat(e.target.value);
              handleFieldChange('temperature', Number.isNaN(parsed) ? undefined : parsed);
            }}
            min="0"
            max="2"
            step="any"
            className={cyberInputClasses}
          />
        </div>

        {/* Retries */}
        <div>
          <label className="block text-xs text-text-muted mb-1">重试次数</label>
          <input
            type="number"
            value={provider.retries || 0}
            onChange={(e) => handleFieldChange('retries', parseInt(e.target.value) || 0)}
            min="0"
            max="10"
            className={cyberInputClasses}
          />
        </div>
      </div>
    </BaseProviderSettings>
  );
}
