import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { CLI_MODES, type CLIMode, type ProviderConfig, type ProviderValidateFn } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

const cyberSelectClasses = "flex h-9 w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-1 text-sm text-slate-100 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 hover:border-violet-400/30 hover:bg-black/50 cursor-pointer appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10";

interface GeminiCLIProviderSettingsProps {
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

export function GeminiCLIProviderSettings({
  provider,
  onUpdate,
  onValidate
}: GeminiCLIProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  const geminiProvider = provider as ProviderConfig & { health_args?: string[]; list_args?: string[] };
  const env = provider.env || {};
  const cliMode: CLIMode =
    provider.cli_mode === CLI_MODES.TUI || provider.cli_mode === CLI_MODES.HEADLESS
      ? provider.cli_mode
      : CLI_MODES.HEADLESS;
  const headlessArgs = Array.isArray(provider.args) && provider.args.length
    ? provider.args
    : ['chat', '--model', '{model}', '--prompt', '{prompt}'];
  const headlessTemplate = [provider.command || 'gemini', ...headlessArgs].join(' ');
  const missingModelPlaceholder = !headlessTemplate.includes('{model}');
  const missingPromptPlaceholder = !headlessTemplate.includes('{prompt}');

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      {/* Gemini CLI Specific Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Gemini CLI 配置</h5>

        {/* CLI Mode */}
        <div>
          <label className="block text-xs text-text-muted mb-1">CLI 模式</label>
          <select
            value={cliMode}
            onChange={(e) => handleFieldChange('cli_mode', e.target.value)}
            className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm"
          >
            <option value={CLI_MODES.HEADLESS}>静默执行（非交互）</option>
            <option value={CLI_MODES.TUI}>TUI（交互）</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            推荐在自动化与测试中使用静默执行模式。
          </p>
        </div>
        
        {/* Google API Key */}
        <div>
          <label className="block text-xs text-text-muted mb-1">Google API 密钥</label>
          <input
            type="text"
            value={env.GOOGLE_API_KEY || ''}
            onChange={(e) => handleFieldChange('env', { ...env, GOOGLE_API_KEY: e.target.value })}
            className={`${cyberInputClasses} font-mono`}
            placeholder="请输入 Google API 密钥"
          />
          <p className="text-[9px] text-text-dim mt-1">
            API Key 可在 <a href="https://aistudio.google.com/app/apikey" target="_blank" className="text-accent hover:underline">Google AI Studio</a> 获取
          </p>
        </div>

        {/* API Key Source */}
        <div>
          <label className="block text-xs text-text-muted mb-1">API 密钥来源</label>
          <select
            value={env.GOOGLE_GENAI_USE_VERTEXAI || 'false'}
            onChange={(e) => handleFieldChange('env', { ...env, GOOGLE_GENAI_USE_VERTEXAI: e.target.value })}
            className={cyberSelectClasses}
          >
            <option value="false">Google AI Studio</option>
            <option value="true">Vertex AI</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            选择 API 密钥来源：Google AI Studio 或 Vertex AI。
          </p>
        </div>

        {cliMode === CLI_MODES.HEADLESS && (
          <>
            {/* Command Arguments */}
            <div>
              <label className="block text-xs text-text-muted mb-1">命令参数（每行一项）</label>
              <textarea
                value={(provider.args || []).join('\n')}
                onChange={(e) => handleFieldChange('args', e.target.value.split('\n').filter(arg => arg.trim()))}
                className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16"
                placeholder="chat --model {'{model}'} --prompt {'{prompt}'}"
              />
              <p className="text-[9px] text-text-dim mt-1">
                Gemini CLI 参数模板。请保留 {'{model}'} 与 {'{prompt}'} 占位符。
              </p>
            </div>

            {/* Headless Template */}
            <div className="bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2">
              <h6 className="text-xs font-semibold text-text-main">静默执行模板</h6>
              <div className="text-[10px] text-text-dim">
                请确保包含 <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">{'{model}'}</code> 与{' '}
                <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">{'{prompt}'}</code>。
              </div>
              <div className="text-[10px] font-mono text-text-main bg-[rgba(35,25,14,0.45)] rounded px-2 py-1 border border-white/10">
                {headlessTemplate}
              </div>
            {(missingModelPlaceholder || missingPromptPlaceholder) && (
              <div className="text-[10px] text-yellow-300">
                  缺失占位符: {missingModelPlaceholder ? '{model}' : ''}
                  {missingModelPlaceholder && missingPromptPlaceholder ? ', ' : ''}
                  {missingPromptPlaceholder ? '{prompt}' : ''}
              </div>
            )}
          </div>
          </>
        )}

        {cliMode === CLI_MODES.TUI && (
          <div className="bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2">
            <h6 className="text-xs font-semibold text-text-main">TUI 使用说明</h6>
            <div className="text-[10px] text-text-dim space-y-1">
              <p><span className="text-text-muted">模型发现:</span> 运行 <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">gemini models list</code></p>
              <p><span className="text-text-muted">交互会话:</span> 运行 <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">gemini chat</code></p>
            </div>
          </div>
        )}

        {/* Health Check Arguments */}
        <div>
          <label className="block text-xs text-text-muted mb-1">健康检查参数</label>
          <input
            type="text"
            value={geminiProvider.health_args?.join(' ') || 'version'}
            onChange={(e) => handleFieldChange('health_args', e.target.value.split(' ').filter(arg => arg.trim()))}
            className={`${cyberInputClasses} font-mono`}
            placeholder="version"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于检查 Gemini CLI 是否可用
          </p>
        </div>

        {/* Model Listing Arguments */}
        <div>
          <label className="block text-xs text-text-muted mb-1">模型列表参数</label>
          <input
            type="text"
            value={geminiProvider.list_args?.join(' ') || 'models list'}
            onChange={(e) => handleFieldChange('list_args', e.target.value.split(' ').filter(arg => arg.trim()))}
            className={`${cyberInputClasses} font-mono`}
            placeholder="models list"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于获取可用模型列表
          </p>
        </div>
      </div>

      {/* Gemini Model Information */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Gemini 模型簿</h5>
        <div className="bg-[rgba(35,25,14,0.45)] rounded-lg p-3 border border-white/10">
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.5-pro</span>
              <span className="text-text-main">• 高阶，2M 上下文</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.5-flash</span>
              <span className="text-text-main">• 快速，1M 上下文</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">gemini-1.0-pro</span>
              <span className="text-text-main">• 旧版模型</span>
            </div>
          </div>
        </div>
        <p className="text-[9px] text-text-dim">
          安装 Gemini CLI：<code className="bg-black/50 px-1 rounded">pip install google-generativeai</code>
        </p>
      </div>

      {/* Environment Variables */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">环境变量</h5>
        <div>
          <label className="block text-xs text-text-muted mb-1">附加环境变量（JSON）</label>
          <textarea
            value={JSON.stringify(env, null, 2)}
            onChange={(e) => {
              try {
                const newEnv = JSON.parse(e.target.value);
                handleFieldChange('env', newEnv);
              } catch {
                // Invalid JSON, don't update
              }
            }}
            className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-20"
            placeholder='{"GOOGLE_GENAI_API_KEY": "你的密钥", "OTHER_VAR": "值"}'
          />
          <p className="text-[9px] text-text-dim mt-1">
            以 JSON 形式补充环境变量
          </p>
        </div>
      </div>
    </BaseProviderSettings>
  );
}
