import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

interface GeminiAPIProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

export function GeminiAPIProviderSettings({
  provider,
  onUpdate,
  onValidate
}: GeminiAPIProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      {/* Gemini API Specific Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Gemini API 配置</h5>
        
        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <input
            type="text"
            value={provider.api_path || '/v1beta/models/{model}:generateContent'}
            onChange={(e) => handleFieldChange('api_path', e.target.value)}
            placeholder="/v1beta/models/{model}:generateContent"
            className={cyberInputClasses}
          />
          <p className="text-[9px] text-text-dim mt-1">
            使用 {'{model}'} 占位符可按模型动态路由
          </p>
        </div>

        {/* Models Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">模型列表路径</label>
          <input
            type="text"
            value={provider.models_path || '/v1beta/models'}
            onChange={(e) => handleFieldChange('models_path', e.target.value)}
            placeholder="/v1beta/models"
            className={cyberInputClasses}
          />
        </div>
      </div>

      {/* Model Information */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Gemini 模型</h5>
        <div className="bg-[rgba(35,25,14,0.45)] rounded-lg p-3 border border-white/10">
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.5-pro</span>
              <span className="text-text-main">• 2M 上下文</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.5-flash</span>
              <span className="text-text-main">• 1M 上下文</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.0-pro</span>
              <span className="text-text-main">• 32K 上下文</span>
            </div>
          </div>
        </div>
        <p className="text-[9px] text-text-dim">
          API Key 可在 <a href="https://aistudio.google.com/app/apikey" target="_blank" className="text-accent hover:underline">Google AI Studio</a> 获取
        </p>
      </div>

      {/* Advanced Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">高级参数</h5>
        
        {/* Temperature */}
        <div>
          <label className="block text-xs text-text-muted mb-1">温度（Temperature）</label>
          <input
            type="number"
            value={provider.temperature || 0.7}
            onChange={(e) => handleFieldChange('temperature', parseFloat(e.target.value) || 0.7)}
            min="0"
            max="2"
            step="0.1"
            className={cyberInputClasses}
          />
        </div>

        {/* Retries */}
        <div>
          <label className="block text-xs text-text-muted mb-1">重试次数</label>
          <input
            type="number"
            value={provider.retries || 3}
            onChange={(e) => handleFieldChange('retries', parseInt(e.target.value) || 3)}
            min="0"
            max="10"
            className={cyberInputClasses}
          />
        </div>
      </div>
    </BaseProviderSettings>
  );
}
