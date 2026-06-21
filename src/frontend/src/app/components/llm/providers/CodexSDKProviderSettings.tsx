import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses, cyberTextareaClasses } from '@/app/components/ui/cyber-input-classes';

interface CodexSDKProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

export function CodexSDKProviderSettings({
  provider,
  onUpdate,
  onValidate
}: CodexSDKProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Codex SDK 配置</h5>

        <div>
          <label className="block text-xs text-text-muted mb-1">默认模型</label>
          <input
            type="text"
            value={provider.default_model || ''}
            onChange={(e) => handleFieldChange('default_model', e.target.value)}
            className={cyberInputClasses}
            placeholder="gpt-4-codex"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-text-muted mb-1">最大重试次数</label>
            <input
              type="number"
              value={provider.max_retries ?? 3}
              onChange={(e) => handleFieldChange('max_retries', parseInt(e.target.value) || 0)}
              className={cyberInputClasses}
              min="0"
              max="10"
            />
          </div>
          <div>
            <label className="block text-xs text-text-muted mb-1">温度（Temperature）</label>
            <input
              type="number"
              value={provider.temperature ?? 0.2}
              onChange={(e) => {
                // NaN-guard so empty input clears to the default instead of 0.
                const parsed = parseFloat(e.target.value);
                handleFieldChange('temperature', Number.isNaN(parsed) ? undefined : parsed);
              }}
              className={cyberInputClasses}
              min="0"
              max="2"
              step="any"
            />
          </div>
        </div>

        <div>
          <label className="flex items-center gap-2 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={provider.thinking_mode ?? true}
              onChange={(e) => handleFieldChange('thinking_mode', e.target.checked)}
              className="rounded border-white/20 bg-[rgba(35,25,14,0.55)]"
            />
            思考模式
          </label>
        </div>

        <div>
          <label className="block text-xs text-text-muted mb-1">SDK 参数（JSON）</label>
          <textarea
            value={JSON.stringify(provider.sdk_params || {}, null, 2)}
            onChange={(e) => {
              try {
                const params = JSON.parse(e.target.value);
                handleFieldChange('sdk_params', params);
              } catch {
                // ignore invalid JSON
              }
            }}
            className={cyberTextareaClasses}
            placeholder='{"organization": "..."}'
          />
          <p className="text-[9px] text-text-dim mt-1">附加 SDK 客户端参数，将合并到构造参数中。</p>
        </div>
      </div>
    </BaseProviderSettings>
  );
}
