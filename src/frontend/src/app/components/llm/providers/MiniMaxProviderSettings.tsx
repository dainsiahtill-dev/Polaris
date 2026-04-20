import React, { useCallback } from 'react';
import { Key } from 'lucide-react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

interface MiniMaxProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

function MiniMaxApiKeyInput({ 
  value, 
  onChange, 
  placeholder 
}: { 
  value?: string; 
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const handleChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value);
  }, [onChange]);

  return (
    <div>
      <label className="block text-xs text-text-muted mb-1 flex items-center gap-1">
        <Key className="size-3" />
        API Key
      </label>
      <input
        type="text"
        data-testid="minimax-api-key-input"
        value={value ?? ''}
        onChange={handleChange}
        placeholder={placeholder || 'sk-...'}
        className={`${cyberInputClasses} font-mono`}
        autoComplete="off"
        spellCheck={false}
      />
      <p className="text-[9px] text-text-dim mt-1">
        API Key用于身份验证，请妥善保管
      </p>
    </div>
  );
}

export function MiniMaxProviderSettings({
  provider,
  onUpdate,
  onValidate
}: MiniMaxProviderSettingsProps) {
  const setFieldValue = useCallback(
    <K extends keyof ProviderConfig>(field: K, value: ProviderConfig[K]) => {
      onUpdate({ [field]: value });
    },
    [onUpdate]
  );

  const handleFieldChange = useCallback((field: string, value: unknown) => {
    setFieldValue(field as keyof ProviderConfig, value);
  }, [setFieldValue]);

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate} hideApiKey hideBaseUrl>
      {/* MiniMax API Configuration */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">MiniMax API 配置</h5>
        
        {/* Base URL */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 基础URL</label>
          <input
            type="text"
            data-testid="minimax-base-url-input"
            value={provider.base_url || ''}
            onChange={(e) => setFieldValue('base_url', e.target.value)}
            placeholder="https://api.minimaxi.com/v1"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">MiniMax官方API端点</p>
        </div>

        {/* API Key */}
        <MiniMaxApiKeyInput
          value={provider.api_key}
          onChange={(value) => setFieldValue('api_key', value)}
          placeholder="sk-..."
        />

        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <input
            type="text"
            data-testid="minimax-api-path-input"
            value={provider.api_path || ''}
            onChange={(e) => setFieldValue('api_path', e.target.value)}
            placeholder="/text/chatcompletion_v2"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">文本对话API路径（v2版本）</p>
        </div>

        {/* Model Input */}
        <div>
          <label className="block text-xs text-text-muted mb-1">模型名称</label>
          <input
            type="text"
            data-testid="minimax-model-id-input"
            value={provider.model || ''}
            onChange={(e) => setFieldValue('model', e.target.value)}
            placeholder="MiniMax-M2.1"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">
            输入 MiniMax 模型名称，如 MiniMax-M2.1、MiniMax-M2.1-lightning、MiniMax-M2
          </p>
        </div>
      </div>

      {/* Model Parameters */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">模型参数</h5>
        
        {/* Temperature */}
        <div>
          <label className="block text-xs text-text-muted mb-1">温度（Temperature，0-1）</label>
          <input
            type="number"
            value={provider.temperature ?? ''}
            onChange={(e) => setFieldValue('temperature', e.target.value === '' ? undefined : parseFloat(e.target.value))}
            placeholder="1.0"
            className={cyberInputClasses}
            min={0}
            max={1}
            step="0.1"
          />
          <p className="text-[9px] text-text-dim mt-1">影响输出随机性，值越高越随机，默认1.0</p>
        </div>

        {/* Stream */}
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="stream"
            checked={provider.streaming ?? false}
            onChange={(e) => handleFieldChange('streaming', e.target.checked)}
            className="rounded border-white/10 bg-[rgba(35,25,14,0.45)]"
          />
          <label htmlFor="stream" className="text-xs text-text-main">
            启用流式传输
          </label>
        </div>
        <p className="text-[9px] text-text-dim mt-1">
          开启后响应将分批返回，适合实时对话场景
        </p>
      </div>

      {/* Model Information */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">MiniMax 模型信息</h5>
        <div className="bg-[rgba(35,25,14,0.45)] rounded-lg p-4 border border-white/10">
          <div className="space-y-3 text-xs">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-emerald-400"></div>
              <span className="text-text-main font-medium">MiniMax-M2 系列</span>
              <span className="text-text-dim">官方文本生成模型</span>
            </div>
            
            <div className="space-y-2 text-text-dim">
              <p>• 请访问 MiniMax 官方文档查看可用模型列表</p>
              <p>• 常用模型：MiniMax-M2.1、MiniMax-M2.1-lightning、MiniMax-M2</p>
              <p>• 支持多轮对话和上下文理解</p>
              <p>• 适用于创意写作、问答对话</p>
            </div>
            
            <div className="pt-2 border-t border-white/10">
              <p className="text-[9px] text-text-dim">
                官方文档：
                <a 
                  href="https://platform.minimax.io/docs/api-reference/api-overview"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cyan-400 hover:text-cyan-300 ml-1"
                >
                  MiniMax API 文档
                </a>
              </p>
            </div>
          </div>
        </div>
      </div>
    </BaseProviderSettings>
  );
}
