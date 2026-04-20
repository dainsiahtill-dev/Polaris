import React, { useState, useCallback, useEffect } from 'react';
import { Key } from 'lucide-react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

interface KimiProviderSettingsProps {
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

const cyberTextareaClasses = "w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 hover:border-violet-400/30 hover:bg-black/50 disabled:opacity-50 disabled:cursor-not-allowed font-mono h-16";

// Predefined Kimi models for quick selection
const KIMI_MODELS = [
  { id: 'kimi-k2.5', context: '256k', description: 'Kimi 迄今最智能的模型，在 Agent、代码、视觉理解及一系列通用智能任务上取得开源 SoTA 表现。同时 Kimi K2.5 也是 Kimi 迄今最全能的模型，原生的多模态架构设计，同时支持视觉与文本输入、思考与非思考模式、对话与 Agent 任务。' },
  { id: 'kimi-k2-0905-preview', context: '256k', description: '在 0711 版本基础上增强了 Agentic Coding 能力、前端代码美观度和实用性、以及上下文理解能力' },
  { id: 'kimi-k2-0711-preview', context: '128k', description: 'MoE 架构基础模型，总参数 1T，激活参数 32B。具备超强代码和 Agent 能力。' },
  { id: 'kimi-k2-turbo-preview', context: '256k', description: 'K2 的高速版本，对标最新版本(0905)。输出速度提升至每秒 60-100 tokens' },
  { id: 'kimi-k2-thinking', context: '256k', description: 'K2 长思考模型，支持  上下文，支持多步工具调用与思考，擅长解决更复杂的问题' },
  { id: 'kimi-k2-thinking-turbo', context: '256k', description: 'K2 长思考模型的高速版本，擅长深度推理，输出速度提升至每秒 60-100 tokens' }
];

function KimiApiKeyInput({ 
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
        data-testid="kimi-api-key-input"
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

export function KimiProviderSettings({
  provider,
  onUpdate,
  onValidate
}: KimiProviderSettingsProps) {
  const serializedHeaders = JSON.stringify(provider.headers || {}, null, 2);
  const [headersText, setHeadersText] = useState(serializedHeaders);

  useEffect(() => {
    setHeadersText(serializedHeaders);
  }, [serializedHeaders]);

  const setFieldValue = useCallback(
    <K extends keyof ProviderConfig>(field: K, value: ProviderConfig[K]) => {
      onUpdate({ [field]: value });
    },
    [onUpdate]
  );

  const handleFieldChange = useCallback((field: string, value: unknown) => {
    setFieldValue(field as keyof ProviderConfig, value);
  }, [setFieldValue]);

  // Get current model value
  const currentModel = provider.model || provider.default_model || 'kimi-k2-thinking';

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate} hideApiKey hideBaseUrl>
      {/* Kimi API Configuration */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">Kimi API 配置</h5>
        
        {/* Base URL */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 基础URL</label>
          <input
            type="text"
            data-testid="kimi-base-url-input"
            value={provider.base_url || ''}
            onChange={(e) => setFieldValue('base_url', e.target.value)}
            placeholder="https://api.moonshot.cn/v1"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">Moonshot AI 官方 API 端点</p>
        </div>

        {/* API Key */}
        <KimiApiKeyInput
          value={provider.api_key}
          onChange={(value) => setFieldValue('api_key', value)}
          placeholder="sk-..."
        />

        {/* API Path */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 路径</label>
          <input
            type="text"
            data-testid="kimi-api-path-input"
            value={provider.api_path || ''}
            onChange={(e) => setFieldValue('api_path', e.target.value)}
            placeholder="/v1/chat/completions"
            className={`${cyberInputClasses} font-mono`}
          />
          <p className="text-[9px] text-text-dim mt-1">对话补全 API 路径（OpenAI 兼容格式）</p>
        </div>

        {/* Custom Headers */}
        <div>
          <label className="block text-xs text-text-muted mb-1">自定义请求头（JSON）</label>
          <textarea
            data-testid="kimi-custom-headers-input"
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
            placeholder='{"x-test-header":"abc123"}'
          />
          <p className="text-[9px] text-text-dim mt-1">
            支持 JSON 或按行填写 `Key: Value`，用于附加鉴权或路由字段。
          </p>
        </div>

        {/* Model Input with Quick Selection */}
        <div>
          <label className="block text-xs text-text-muted mb-1">模型</label>
          <input
            type="text"
            data-testid="kimi-model-id-input"
            value={currentModel}
            onChange={(e) => handleFieldChange('model', e.target.value)}
            placeholder="kimi-k2-thinking"
            className={`${cyberInputClasses} font-mono`}
          />
          
          {/* Quick Selection Buttons */}
          <div className="mt-2 flex flex-wrap gap-2">
            {KIMI_MODELS.map((model) => (
              <button
                key={model.id}
                type="button"
                onClick={() => handleFieldChange('model', model.id)}
                className={`text-[9px] px-2 py-1 rounded border transition-colors ${
                  currentModel === model.id
                    ? 'bg-violet-500/20 border-violet-500/50 text-violet-200'
                    : 'bg-[rgba(35,25,14,0.45)] border-white/10 hover:border-white/20 text-text-dim'
                }`}
              >
                {model.id}
                <span className="ml-1 opacity-70">({model.context})</span>
              </button>
            ))}
          </div>
          
          <p className="text-[9px] text-text-dim mt-2">
            支持多轮对话、流式输出、多模态输入（文本、图片、视频）
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

        {/* Top P */}
        <div>
          <label className="block text-xs text-text-muted mb-1">Top P（核采样，0-1）</label>
          <input
            type="number"
            value={provider.top_p ?? ''}
            onChange={(e) => setFieldValue('top_p', e.target.value === '' ? undefined : parseFloat(e.target.value))}
            placeholder="0.95"
            className={cyberInputClasses}
            min={0}
            max={1}
            step="0.01"
          />
          <p className="text-[9px] text-text-dim mt-1">核采样阈值，默认0.95</p>
        </div>

        {/* Stream */}
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="kimi-stream"
            checked={provider.streaming ?? false}
            onChange={(e) => handleFieldChange('streaming', e.target.checked)}
            className="rounded border-white/10 bg-[rgba(35,25,14,0.45)]"
          />
          <label htmlFor="kimi-stream" className="text-xs text-text-main">
            启用流式传输
          </label>
        </div>
        <p className="text-[9px] text-text-dim mt-1">
          开启后响应将分批返回，适合实时对话场景
        </p>
      </div>

      {/* Kimi Model Information */}
      <div className="space-y-4">
        <h5 className="text-xs font-semibold text-text-main">Kimi 模型信息</h5>
        <div className="bg-[rgba(35,25,14,0.45)] rounded-lg p-4 border border-white/10">
          <div className="space-y-3 text-xs">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-violet-400"></div>
              <span className="text-text-main font-medium">Moonshot AI (Kimi)</span>
              <span className="text-text-dim">官方大语言模型</span>
            </div>
            
            <div className="space-y-2 text-text-dim">
              <p>Kimi 是 Moonshot AI 推出的系列大语言模型，具备强大的通用智能能力和超大上下文窗口，适用于对话、代码生成、视觉理解等多种任务。</p>
              <p>• kimi-k2.5：Kimi 迄今最智能的模型，在 Agent、代码、视觉理解及一系列通用智能任务上取得开源 SoTA 表现。同时 Kimi K2.5 也是 Kimi 迄今最全能的模型，原生的多模态架构设计，同时支持视觉与文本输入、思考与非思考模式、对话与 Agent 任务。</p>
              <p>• kimi-k2-0905-preview：在 0711 版本基础上增强了 Agentic Coding 能力、前端代码美观度和实用性、以及上下文理解能力</p>
              <p>• kimi-k2-0711-preview：MoE 架构基础模型，总参数 1T，激活参数 32B。具备超强代码和 Agent 能力。</p>
              <p>• kimi-k2-thinking：K2 长思考模型，支持 256K 上下文窗口</p>
              <p>• kimi-k2-turbo-preview：K2 的高速版本，支持 256K 上下文窗口</p>
              <p>• 支持多轮对话、流式输出、多模态输入</p>
            </div>
            
            <div className="pt-2 border-t border-white/10">
              <p className="text-[9px] text-text-dim">
                官方文档：
                <a 
                  href="https://platform.moonshot.cn/docs/api/chat"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cyan-400 hover:text-cyan-300 ml-1"
                >
                  Kimi API 文档
                </a>
              </p>
            </div>
          </div>
        </div>
      </div>
    </BaseProviderSettings>
  );
}
