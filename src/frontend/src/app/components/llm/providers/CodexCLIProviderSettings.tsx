import React from 'react';
import { BaseProviderSettings } from './BaseProviderSettings';
import { CodexModelBrowser } from '../model-browser/CodexModelBrowser';
import { CLI_MODES, type CLIMode, type ProviderConfig, type ProviderValidateFn } from '../types';
import type { CodexExecConfig } from '../types/strict';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';

const cyberSelectClasses = "flex h-9 w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-1 text-sm text-slate-100 transition-all duration-200 outline-none focus:border-violet-500/50 focus:ring-2 focus:ring-violet-500/20 focus:bg-black/60 hover:border-violet-400/30 hover:bg-black/50 cursor-pointer appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10";

interface CodexCLIProviderSettingsProps {
  providerId?: string;
  provider: ProviderConfig;
  onUpdate: (updates: Partial<ProviderConfig>) => void;
  onValidate: ProviderValidateFn;
}

export function CodexCLIProviderSettings({
  providerId,
  provider,
  onUpdate,
  onValidate
}: CodexCLIProviderSettingsProps) {
  const handleFieldChange = (field: string, value: unknown) => {
    onUpdate({ [field]: value });
  };

  const codexExec = (provider.codex_exec || {}) as CodexExecConfig & Record<string, unknown>;
  const codexExecColor = (codexExec.color || 'never') as 'never' | 'auto' | 'always';
  const codexExecSandbox = (codexExec.sandbox || 'read-only') as 'read-only' | 'workspace-write' | 'danger-full-access';
  const codexExecProfile = (codexExec.profile || '') as string;
  const codexExecConfig = (codexExec.config || []) as string[];
  const codexExecAddDirs = (codexExec.add_dirs || []) as string[];
  const codexExecOutputSchema = (codexExec.output_schema || '') as string;
  const codexExecOutputLastMessage = (codexExec.output_last_message || '') as string;
  const modelId = typeof provider.model === 'string' ? provider.model : '';
  const codexExecConfigArr = Array.isArray(codexExecConfig) ? codexExecConfig : [];
  const cliMode: CLIMode =
    provider.cli_mode === CLI_MODES.TUI || provider.cli_mode === CLI_MODES.HEADLESS
      ? provider.cli_mode
      : CLI_MODES.HEADLESS;

  const getConfigOverrideValue = (key: string): string | null => {
    for (const entry of codexExecConfigArr) {
      const eqIndex = entry.indexOf('=');
      if (eqIndex <= 0) {
        continue;
      }
      const entryKey = entry.slice(0, eqIndex).trim();
      if (entryKey !== key) {
        continue;
      }
      const rawValue = entry.slice(eqIndex + 1).trim();
      if (
        (rawValue.startsWith('"') && rawValue.endsWith('"')) ||
        (rawValue.startsWith("'") && rawValue.endsWith("'"))
      ) {
        return rawValue.slice(1, -1);
      }
      return rawValue;
    }
    return null;
  };

  const upsertConfigOverride = (key: string, value: string | null): string[] => {
    const updated = codexExecConfigArr
      .map((entry: string) => {
        const eqIndex = entry.indexOf('=');
        if (eqIndex <= 0) {
          return entry;
        }
        const entryKey = entry.slice(0, eqIndex).trim();
        if (entryKey !== key) {
          return entry;
        }
        return value ? `${key}=${value}` : null;
      })
      .filter((entry): entry is string => Boolean(entry));

    const hasKey = updated.some((entry: string) => entry.split('=', 1)[0].trim() === key);
    if (value && !hasKey) {
      updated.push(`${key}=${value}`);
    }
    return updated;
  };

  const approvalPolicyOverride = getConfigOverrideValue('approval_policy');
  const approvalPolicyValue =
    approvalPolicyOverride ??
    (typeof codexExec.ask_for_approval === 'string' && codexExec.ask_for_approval
      ? codexExec.ask_for_approval
      : 'auto');

  const reasoningEffortOverride = getConfigOverrideValue('model_reasoning_effort');
  const reasoningEffortValue = reasoningEffortOverride || 'auto';

  const updateConfigOverride = (
    key: string,
    value: string | null,
    extraUpdates: Record<string, unknown> = {}
  ) => {
    const nextOverrides = upsertConfigOverride(key, value);
    handleFieldChange('codex_exec', { ...codexExec, ...extraUpdates, config: nextOverrides });
  };

  const buildHeadlessArgs = (): string[] => {
    const opts = typeof codexExec === 'object' && codexExec ? codexExec : {};
    const args: string[] = ['exec'];

    const cd = String((opts as Record<string, unknown>).cd || '').trim();
    if (cd) {
      args.push('--cd', cd);
    }

    const color = String((opts as Record<string, unknown>).color || '').trim();
    if (['always', 'never', 'auto'].includes(color)) {
      args.push('--color', color);
    }

    if ((opts as Record<string, unknown>).skip_git_repo_check !== false) {
      args.push('--skip-git-repo-check');
    }

    const sandbox = String((opts as Record<string, unknown>).sandbox || '').trim();
    if (sandbox) {
      args.push('--sandbox', sandbox);
    }

    args.push('--model', '{model}');

    const jsonMode = (opts as Record<string, unknown>).json;
    if (jsonMode !== false) {
      args.push(jsonMode === 'experimental' ? '--experimental-json' : '--json');
    }

    // codex exec no longer supports --ask-for-approval; avoid rendering it in templates.

    if ((opts as Record<string, unknown>).oss) {
      args.push('--oss');
    }

    const addDirs = (opts as Record<string, unknown>).add_dirs;
    if (Array.isArray(addDirs)) {
      addDirs.filter(Boolean).forEach((entry) => {
        args.push('--add-dir', String(entry));
      });
    }

    const images = (opts as Record<string, unknown>).images;
    if (Array.isArray(images)) {
      images.filter(Boolean).forEach((entry) => {
        args.push('--image', String(entry));
      });
    }

    const outputSchema = String((opts as Record<string, unknown>).output_schema || '').trim();
    if (outputSchema) {
      args.push('--output-schema', outputSchema);
    }

    const outputLast = String((opts as Record<string, unknown>).output_last_message || '').trim();
    if (outputLast) {
      args.push('--output-last-message', outputLast);
    }

    const profile = String((opts as Record<string, unknown>).profile || '').trim();
    if (profile) {
      args.push('--profile', profile);
    }

    const configOverrides = (opts as Record<string, unknown>).config;
    if (Array.isArray(configOverrides)) {
      configOverrides.filter(Boolean).forEach((entry) => {
        args.push('--config', String(entry));
      });
    }

    if ((opts as Record<string, unknown>).yolo) {
      args.push('--yolo');
    } else if ((opts as Record<string, unknown>).full_auto) {
      args.push('--full-auto');
    }

    args.push('{prompt}');
    return args;
  };

  const headlessTemplate = [provider.command || 'codex', ...buildHeadlessArgs()].join(' ');
  const missingModelPlaceholder = !headlessTemplate.includes('{model}');
  const missingPromptPlaceholder = !headlessTemplate.includes('{prompt}');

  return (
    <BaseProviderSettings provider={provider} onUpdate={onUpdate} onValidate={onValidate}>
      {/* Codex CLI Specific Settings */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">Codex CLI 配置</h5>

        {/* Model ID */}
        <div>
          <label className="block text-xs text-text-muted mb-1">模型 ID</label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={modelId}
              onChange={(e) => handleFieldChange('model', e.target.value)}
              className={`${cyberInputClasses} flex-1 font-mono`}
              placeholder="例如 gpt-5.2-codex、o3-mini"
            />
            {providerId ? (
              <CodexModelBrowser
                providerId={providerId}
                command={provider.command}
                tuiArgs={provider.tui_args}
                env={provider.env}
                modelId={modelId}
                onSelect={(value) => handleFieldChange('model', value)}
              />
            ) : (
              <button
                type="button"
                disabled
                className="px-3 py-2 text-[10px] font-semibold border border-white/10 text-text-dim rounded"
              >
                模型列表
              </button>
            )}
          </div>
          <p className="text-[9px] text-text-dim mt-1">
            使用 TUI 查看 /model 列表后选择模型 ID。
          </p>
        </div>

        {/* CLI Mode */}
        <div>
          <label className="block text-xs text-text-muted mb-1">CLI 模式</label>
          <select
            value={cliMode}
            onChange={(e) => handleFieldChange('cli_mode', e.target.value)}
            className={cyberSelectClasses}
          >
            <option value={CLI_MODES.HEADLESS}>静默执行（exec --json）</option>
            <option value={CLI_MODES.TUI}>TUI（交互）</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            自动化建议使用静默执行；人工探查模型时使用交互模式。
          </p>
        </div>

        {/* Approval Policy */}
        <div>
          <label className="block text-xs text-text-muted mb-1">审批策略</label>
          <select
            value={approvalPolicyValue}
            onChange={(e) => {
              const value = e.target.value;
              if (value === 'auto') {
                updateConfigOverride('approval_policy', null, { ask_for_approval: '' });
                return;
              }
              updateConfigOverride('approval_policy', `"${value}"`, { ask_for_approval: value });
            }}
            className={cyberSelectClasses}
          >
            <option value="auto">自动（沿用 profile）</option>
            <option value="untrusted">不信任（始终询问）</option>
            <option value="on-failure">失败时询问</option>
            <option value="on-request">按请求询问</option>
            <option value="never">从不询问</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            将写入 <span className="font-mono">--config approval_policy=...</span>（覆盖 profile 默认值）。
          </p>
        </div>

        {/* Reasoning Effort */}
        <div>
          <label className="block text-xs text-text-muted mb-1">推理强度</label>
          <select
            value={reasoningEffortValue}
            onChange={(e) => {
              const value = e.target.value;
              if (value === 'auto') {
                updateConfigOverride('model_reasoning_effort', null);
                return;
              }
              updateConfigOverride('model_reasoning_effort', `"${value}"`);
            }}
            className={cyberSelectClasses}
          >
            <option value="auto">自动（沿用 profile）</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            部分模型仅支持部分档位（如 medium/high），自动档将沿用 profile 设置。
          </p>
        </div>

        {/* Sandbox Mode */}
        <div>
          <label className="block text-xs text-text-muted mb-1">沙箱策略</label>
          <select
            value={codexExecSandbox}
            onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, sandbox: e.target.value })}
            className={cyberSelectClasses}
          >
            <option value="read-only">只读（安全默认）</option>
            <option value="workspace-write">工作区可写</option>
            <option value="danger-full-access">完全放开（高风险）</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            控制生成命令的可执行范围；只读最安全。
          </p>
        </div>

        {/* JSON Mode */}
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={codexExec.json !== false}
              onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, json: e.target.checked })}
              className="rounded border-white/20 bg-[rgba(35,25,14,0.45)]"
            />
            <span>JSON 模式</span>
          </label>
          <p className="text-[9px] text-text-dim ml-5">
            输出 JSON 事件，便于 Polaris 自动处理
          </p>
        </div>

        {/* Color Output */}
        <div>
          <label className="block text-xs text-text-muted mb-1">彩色输出</label>
          <select
            value={codexExecColor}
            onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, color: e.target.value })}
            className={cyberSelectClasses}
          >
            <option value="never">从不</option>
            <option value="auto">自动</option>
            <option value="always">总是</option>
          </select>
          <p className="text-[9px] text-text-dim mt-1">
            控制 ANSI 颜色输出（JSON 模式通常禁用）
          </p>
        </div>

        {/* Additional Options */}
        <div className="space-y-3">
          <h5 className="text-xs font-semibold text-text-main">自动化选项</h5>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={codexExec.skip_git_repo_check !== false}
                onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, skip_git_repo_check: e.target.checked })}
                className="rounded border-white/20 bg-[rgba(35,25,14,0.45)]"
              />
              <span>跳过 Git 仓库检查</span>
            </label>
            <p className="text-[9px] text-text-dim ml-5">
              允许在非 Git 仓库执行（请谨慎）
            </p>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={codexExec.full_auto === true}
                onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, full_auto: e.target.checked })}
                className="rounded border-white/20 bg-[rgba(35,25,14,0.45)]"
              />
              <span>全自动模式</span>
            </label>
            <p className="text-[9px] text-text-dim ml-5">
              使用自动化预设（工作区可写 + 按请求审批）
            </p>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={codexExec.yolo === true}
                onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, yolo: e.target.checked })}
                className="rounded border-white/20 bg-[rgba(35,25,14,0.45)]"
              />
              <span>YOLO 模式（高风险）</span>
            </label>
            <p className="text-[9px] text-text-dim ml-5 text-red-400">
              ⚠️ 跳过所有审批与沙箱限制，仅建议在隔离环境使用！
            </p>
          </div>

          <div className="space-y-2">
            <label className="flex items-center gap-2 text-xs text-text-muted">
              <input
                type="checkbox"
                checked={codexExec.oss === true}
                onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, oss: e.target.checked })}
                className="rounded border-white/20 bg-[rgba(35,25,14,0.45)]"
              />
              <span>OSS 提供商模式</span>
            </label>
            <p className="text-[9px] text-text-dim ml-5">
              使用本地 OSS 提供商（需本地 Ollama 运行）
            </p>
          </div>
        </div>

        {cliMode === CLI_MODES.HEADLESS && (
          <div className="bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2">
            <h6 className="text-xs font-semibold text-text-main">静默执行模板</h6>
            <div className="text-[10px] text-text-dim">
              请确保包含 <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">{'{model}'}</code> 与{' '}
              <code className="bg-[rgba(35,25,14,0.55)] px-1 rounded">{'{prompt}'}</code> 占位符。
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
        )}

        {cliMode === CLI_MODES.TUI && (
          <>
            {/* Manual Model Entry (for TUI mode) */}
            <div>
              <label className="block text-xs text-text-muted mb-1">手动模型录入</label>
              <textarea
                value={(provider as ProviderConfig & { manual_models?: string[] }).manual_models?.join('\n') || ''}
                onChange={(e) =>
                  handleFieldChange(
                    'manual_models',
                    e.target.value.split('\n').filter((model) => model.trim())
                  )
                }
                className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16"
                placeholder="gpt-4-codex&#10;gpt-5.2-codex&#10;custom-model"
              />
              <p className="text-[9px] text-text-dim mt-1">
                可手动填写模型名；运行 `codex` 后输入 `/models` 可查看可用模型。
              </p>
            </div>

            {/* TUI Instructions */}
            <div className="bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2">
              <h6 className="text-xs font-semibold text-text-main">TUI 使用说明</h6>
              <div className="text-[10px] text-text-dim space-y-1">
                <p><span className="text-text-muted">模型发现:</span> 运行 `codex` → 输入 `/models`</p>
                <p><span className="text-text-muted">会话状态:</span> 运行 `codex` → 输入 `/status`</p>
                <p><span className="text-text-muted">权限设置:</span> 运行 `codex` → 输入 `/permissions`</p>
                <p><span className="text-text-muted">帮助文档:</span> 运行 `codex` → 输入 `/help`</p>
              </div>
            </div>
          </>
        )}

        {/* Profile */}
        <div>
          <label className="block text-xs text-text-muted mb-1">Profile 档案</label>
          <input
            type="text"
            value={codexExecProfile}
            onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, profile: e.target.value })}
            className={cyberInputClasses}
            placeholder="默认（default）、codex 或自定义档案名"
          />
          <p className="text-[9px] text-text-dim mt-1">
            从 `~/.codex/config.toml` 加载预设 profile
          </p>
        </div>

        {/* Configuration Overrides */}
        <div>
          <label className="block text-xs text-text-muted mb-1">配置覆盖（key=value）</label>
          <textarea
            value={codexExecConfig.join('\n')}
            onChange={(e) => handleFieldChange('codex_exec', {
              ...codexExec,
              config: e.target.value.split('\n').filter(config => config.trim() && config.includes('='))
            })}
            className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16"
            placeholder="key1=value1&#10;key2=value2"
          />
          <p className="text-[9px] text-text-dim mt-1">
            支持 TOML 值。示例：<span className="font-mono">web_search=&quot;live&quot;</span>、{' '}
            <span className="font-mono">shell_environment_policy.include_only=[&quot;PATH&quot;,&quot;HOME&quot;]</span>
          </p>
        </div>

        {/* Additional Directories */}
        <div>
          <label className="block text-xs text-text-muted mb-1">附加目录授权</label>
          <textarea
            value={codexExecAddDirs.join('\n')}
            onChange={(e) => handleFieldChange('codex_exec', {
              ...codexExec,
              add_dirs: e.target.value.split('\n').filter(dir => dir.trim())
            })}
            className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16"
            placeholder="/path/to/dir1&#10;/path/to/dir2"
          />
          <p className="text-[9px] text-text-dim mt-1">
            为工作区外目录授予写权限
          </p>
        </div>

        {/* Output Schema */}
        <div>
          <label className="block text-xs text-text-muted mb-1">输出 Schema</label>
          <input
            type="text"
            value={codexExecOutputSchema}
            onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, output_schema: e.target.value })}
            className={`${cyberInputClasses} font-mono`}
            placeholder="/path/to/schema.json"
          />
          <p className="text-[9px] text-text-dim mt-1">
            用于校验最终输出的 JSON Schema 文件（便于流水线）
          </p>
        </div>

        {/* Output Last Message */}
        <div>
          <label className="block text-xs text-text-muted mb-1">末条消息输出路径</label>
          <input
            type="text"
            value={codexExecOutputLastMessage}
            onChange={(e) => handleFieldChange('codex_exec', { ...codexExec, output_last_message: e.target.value })}
            className={`${cyberInputClasses} font-mono`}
            placeholder="runtime/CODEX_LAST_MESSAGE.md"
          />
          <p className="text-[9px] text-text-dim mt-1">
            将最后一条助手消息写入文件，便于脚本续跑
          </p>
        </div>
      </div>

      {/* Environment Variables */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">环境变量</h5>
        <div>
          <label className="block text-xs text-text-muted mb-1">环境变量（JSON）</label>
          <textarea
            value={JSON.stringify(provider.env || {}, null, 2)}
            onChange={(e) => {
              try {
                const env = JSON.parse(e.target.value);
                handleFieldChange('env', env);
              } catch {
                // Invalid JSON, don't update
              }
            }}
            className="w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-20"
            placeholder='{"KEY": "value"}'
          />
          <p className="text-[9px] text-text-dim mt-1">
            使用 JSON 传入环境变量。可引用 keychain 值，例如
            <span className="font-mono"> keychain:llm:minimax </span>
            （或 <span className="font-mono">${'{'}keychain:llm:minimax{'}'}</span>），运行时将自动解析。
          </p>
        </div>
      </div>

    </BaseProviderSettings>
  );
}

