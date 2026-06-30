import React, { useEffect, useState } from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses, cyberTextareaClasses } from '@/app/components/ui/cyber-input-classes';

interface OpenAIProviderSettingsProps {
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

export function OpenAIProviderSettings({
  provider,
  onUpdate,
  onValidate
}: OpenAIProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };
  const serializedHeaders = JSON.stringify(provider.headers || {}, null, 2);
  const [headersText, setHeadersText] = useState(serializedHeaders);
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
      {/* OpenAI Compatible Specific Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">OpenAI 兼容配置</h5>
        
        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <input
            type="text"
            data-testid="openai-api-path-input"
            value={provider.api_path || '/v1/chat/completions'}
            onChange={(e) => handleFieldChange('api_path', e.target.value)}
            className={`${cyberInputClasses} font-mono`}
            placeholder="/v1/chat/completions"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于连通性测试的对话补全接口地址
          </p>
        </div>

        {/* Custom Headers */}
        <div>
          <label className="block text-xs text-text-muted mb-1">自定义请求头（JSON）</label>
          <textarea
            data-testid="openai-custom-headers-input"
            value={headersText}
            onChange={(e) => {
              const nextValue = e.target.value;
              setHeadersText(nextValue);

              const parsedHeaders = parseCustomHeadersInput(nextValue);
              if (parsedHeaders) {
                handleFieldChange('headers', parsedHeaders);
              }
            }}
            className={`${cyberTextareaClasses} font-mono h-16`}
            placeholder='{"Custom-Header": "value"}'
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
            data-testid="openai-model-id-input"
            value={modelId}
            onChange={(e) => handleFieldChange('model', e.target.value)}
            placeholder="请输入兼容模型 ID"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">
            支持 OpenAI 兼容服务的第三方模型 ID。
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
              // ?? + NaN-guard (NOT `|| default`): a temperature of 0 is falsy,
              // so `0 || 0.2` silently forced 0.2 and made 0.1 the lowest
              // settable value. Empty input clears back to the default.
              const parsed = parseFloat(e.target.value);
              handleFieldChange('temperature', Number.isNaN(parsed) ? undefined : parsed);
            }}
            className={cyberInputClasses}
            min="0"
            max="2"
            step="any"
          />
        </div>

        {/* Retries */}
        <div>
          <label className="block text-xs text-text-muted mb-1">重试次数</label>
          <input
            type="number"
            value={provider.retries || 0}
            onChange={(e) => handleFieldChange('retries', parseInt(e.target.value) || 0)}
            className={cyberInputClasses}
            min="0"
            max="10"
          />
        </div>
      </div>
    </BaseProviderSettings>
  );
}
