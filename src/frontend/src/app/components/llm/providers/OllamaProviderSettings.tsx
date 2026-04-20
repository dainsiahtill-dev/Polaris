import React, { useState, useEffect } from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { devLogger } from '@/app/utils/devLogger';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { RefreshCw, Check, AlertCircle } from 'lucide-react';

interface OllamaProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

const cyberInputClasses = "flex h-9 w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-1 text-sm text-slate-100 placeholder:text-slate-500 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-[rgba(35,25,14,0.75)] hover:border-violet-400/30 hover:bg-[rgba(35,25,14,0.7)] disabled:opacity-50 disabled:cursor-not-allowed";
const cyberSelectClasses = "flex h-9 w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-1 text-sm text-slate-100 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 hover:border-violet-400/30 hover:bg-black/50 cursor-pointer appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10";

export function OllamaProviderSettings({
  provider,
  onUpdate,
  onValidate
}: OllamaProviderSettingsProps) {
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [isCustomModel, setIsCustomModel] = useState(false);

  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  const fetchModels = async () => {
    setIsLoadingModels(true);
    setModelError(null);
    const baseUrl = provider.base_url || 'http://127.0.0.1:11434';
    
    try {
      // Clean up base URL to ensure valid fetch
      const url = new URL('/api/tags', baseUrl).toString();
      const response = await fetch(url);

      if (!response.ok) {
        throw new Error(`连接失败：${response.statusText}`);
      }

      const data = await response.json();
      interface OllamaModel {
        name: string;
        model?: string;
        modified_at?: string;
        size?: number;
      }
      const models = (data.models || []).map((m: OllamaModel) => m.name);
      setAvailableModels(models);
      
      // If current model is not in list and not empty, set as custom
      if (provider.model && !models.includes(provider.model)) {
        setIsCustomModel(true);
      }
    } catch (error) {
      devLogger.error('Failed to fetch Ollama models:', error);
      setModelError(error instanceof Error ? error.message : '获取模型列表失败');
    } finally {
      setIsLoadingModels(false);
    }
  };

  // Initial fetch if URL is present
  useEffect(() => {
    if (provider.base_url) {
      fetchModels();
    }
  }, []); // Only modify this if we want auto-refetch on URL change, but manual is safer for edits

  const handleModelSelect = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const value = e.target.value;
    if (value === 'custom') {
      setIsCustomModel(true);
      // Don't clear model immediately to allow "editing" current if valid, 
      // or clear if starting fresh. For now keep current.
    } else {
      setIsCustomModel(false);
      handleFieldChange('model', value);
    }
  };

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      {/* Ollama Specific Settings */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">Ollama 配置</h5>
        
        {/* Base URL */}
        <div>
          <label className="block text-xs text-text-muted mb-1">Ollama 服务 URL</label>
          <div className="flex gap-2">
            <input
              type="text"
              data-testid="ollama-base-url-input"
              value={provider.base_url || 'http://127.0.0.1:11434'}
              onChange={(e) => handleFieldChange('base_url', e.target.value)}
              className={`${cyberInputClasses} flex-1 font-mono`}
              placeholder="http://127.0.0.1:11434"
            />
          </div>
          <p className="text-[9px] text-text-dim mt-1">
            本地 Ollama 服务地址（默认：http://127.0.0.1:11434）
          </p>
        </div>

        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <select
            data-testid="ollama-api-path-select"
            value={provider.api_path || '/api/chat'}
            onChange={(e) => handleFieldChange('api_path', e.target.value)}
            className={cyberSelectClasses}
          >
            <option value="/api/chat">聊天接口（/api/chat）</option>
            <option value="/api/generate">生成接口（/api/generate）</option>
            <option value="/v1/chat/completions">OpenAI 兼容（/v1/chat/completions）</option>
          </select>
        </div>

        {/* API Key - Only show in OpenAI compatibility mode */}
        {(provider.api_path || '').startsWith('/v1/') && (
          <div>
            <label className="block text-xs text-text-muted mb-1">API Key</label>
            <input
              type="password"
              data-testid="ollama-api-key-input"
              value={provider.api_key || 'ollama'}
              onChange={(e) => handleFieldChange('api_key', e.target.value)}
              className={`${cyberInputClasses} font-mono`}
              placeholder="ollama"
            />
            <p className="text-[9px] text-text-dim mt-1">
              OpenAI 兼容模式需要 API Key（可使用占位符 "ollama"）
            </p>
          </div>
        )}

        {/* Model Selection */}
        <div className="bg-[rgba(35,25,14,0.3)] rounded-lg p-3 border border-white/5">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-text-main">模型选择</label>
            <button
              type="button"
              onClick={fetchModels}
              disabled={isLoadingModels}
              className="text-[10px] flex items-center gap-1 text-cyan-400 hover:text-cyan-300 disabled:opacity-50"
            >
              <RefreshCw className={`size-3 ${isLoadingModels ? 'animate-spin' : ''}`} />
              {isLoadingModels ? '扫描中...' : '刷新模型'}
            </button>
          </div>

          <div className="space-y-2">
            <select
              data-testid="ollama-model-select"
              value={isCustomModel ? 'custom' : (provider.model || '')}
              onChange={handleModelSelect}
              className={cyberSelectClasses}
            >
              <option value="" disabled>请选择模型...</option>
              {availableModels.map(model => (
                <option key={model} value={model}>{model}</option>
              ))}
              <option value="custom">自定义 / 手动输入...</option>
            </select>

            {isCustomModel && (
              <div className="animate-in fade-in slide-in-from-top-1">
                <input
                  type="text"
                  data-testid="ollama-model-id-input"
                  value={provider.model || ''}
                  onChange={(e) => handleFieldChange('model', e.target.value)}
                  className="flex h-9 w-full min-w-0 rounded-md border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-sm text-slate-100 placeholder:text-slate-500 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 font-mono"
                  placeholder="请输入模型名（如 llama3:8b）"
                  autoFocus
                />
                <p className="text-[9px] text-indigo-300 mt-1 flex items-center gap-1">
                  <AlertCircle className="size-3" />
                  已启用手动输入，请确认该模型已在 Ollama 中拉取。
                </p>
              </div>
            )}
            
            {modelError && (
              <div className="text-[10px] text-rose-300 bg-rose-500/10 px-2 py-1.5 rounded border border-rose-500/20 flex items-start gap-1.5">
                 <AlertCircle className="size-3 shrink-0 mt-0.5" />
                 <div>
                   <p className="font-semibold">连接失败</p>
                   <p className="opacity-80">{modelError}</p>
                 </div>
              </div>
            )}
            
            {!modelError && availableModels.length > 0 && (
              <div className="text-[9px] text-emerald-400/80 flex items-center gap-1 px-1">
                <Check className="size-3" />
                已发现 {availableModels.length} 个本地模型
              </div>
            )}
          </div>
        </div>

      </div>
    </BaseProviderSettings>
  );
}
