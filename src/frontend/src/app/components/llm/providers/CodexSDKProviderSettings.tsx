import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

const cyberTextareaClasses = "flex w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 hover:border-violet-400/30 hover:bg-black/50 disabled:opacity-50 disabled:cursor-not-allowed min-h-[80px] resize-y";

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
              onChange={(e) => handleFieldChange('temperature', parseFloat(e.target.value) || 0)}
              className={cyberInputClasses}
              min="0"
              max="2"
              step="0.1"
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
