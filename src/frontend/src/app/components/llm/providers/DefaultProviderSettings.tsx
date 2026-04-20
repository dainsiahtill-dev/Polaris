import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';

interface DefaultProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

export function DefaultProviderSettings({
  provider,
  onUpdate,
  onValidate
}: DefaultProviderSettingsProps) {
  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">通用提供商设置</h5>
        <div className="bg-black/30 rounded-lg p-3 border border-white/10">
          <p className="text-xs text-text-dim">
            当前提供商使用默认配置；可用的专属参数取决于提供商类型。
          </p>
        </div>
      </div>
    </BaseProviderSettings>
  );
}
