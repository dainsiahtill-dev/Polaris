import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { BaseProviderSettings } from './BaseProviderSettings';
import { CodexModelBrowser } from '../model-browser/CodexModelBrowser';
import { CLI_MODES } from '../types';
import { cyberInputClasses } from '@/app/components/ui/cyber-input-classes';
const cyberSelectClasses = "flex h-9 w-full min-w-0 rounded-md border border-white/10 bg-[rgba(35,25,14,0.55)] px-3 py-1 text-sm text-slate-100 transition-all duration-200 outline-none focus:border-slate-400/50 focus:ring-2 focus:ring-slate-400/20 focus:bg-black/60 hover:border-slate-400/30 hover:bg-black/50 cursor-pointer appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2224%22%20height%3D%2224%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%2394a3b8%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpolyline%20points%3D%226%209%2012%2015%2018%209%22%3E%3C%2Fpolyline%3E%3C%2Fsvg%3E')] bg-[length:16px] bg-[right_8px_center] bg-no-repeat pr-10";
export function CodexCLIProviderSettings({ providerId, provider, onUpdate, onValidate }) {
    const handleFieldChange = (field, value) => {
        onUpdate({ [field]: value });
    };
    const codexExec = (provider.codex_exec || {});
    const codexExecColor = (codexExec.color || 'never');
    const codexExecSandbox = (codexExec.sandbox || 'read-only');
    const codexExecProfile = (codexExec.profile || '');
    const codexExecConfig = (codexExec.config || []);
    const codexExecAddDirs = (codexExec.add_dirs || []);
    const codexExecOutputSchema = (codexExec.output_schema || '');
    const codexExecOutputLastMessage = (codexExec.output_last_message || '');
    const modelId = typeof provider.model === 'string' ? provider.model : '';
    const codexExecConfigArr = Array.isArray(codexExecConfig) ? codexExecConfig : [];
    const cliMode = provider.cli_mode === CLI_MODES.TUI || provider.cli_mode === CLI_MODES.HEADLESS
        ? provider.cli_mode
        : CLI_MODES.HEADLESS;
    const getConfigOverrideValue = (key) => {
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
            if ((rawValue.startsWith('"') && rawValue.endsWith('"')) ||
                (rawValue.startsWith("'") && rawValue.endsWith("'"))) {
                return rawValue.slice(1, -1);
            }
            return rawValue;
        }
        return null;
    };
    const upsertConfigOverride = (key, value) => {
        const updated = codexExecConfigArr
            .map((entry) => {
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
            .filter((entry) => Boolean(entry));
        const hasKey = updated.some((entry) => entry.split('=', 1)[0].trim() === key);
        if (value && !hasKey) {
            updated.push(`${key}=${value}`);
        }
        return updated;
    };
    const approvalPolicyOverride = getConfigOverrideValue('approval_policy');
    const approvalPolicyValue = approvalPolicyOverride ??
        (typeof codexExec.ask_for_approval === 'string' && codexExec.ask_for_approval
            ? codexExec.ask_for_approval
            : 'auto');
    const reasoningEffortOverride = getConfigOverrideValue('model_reasoning_effort');
    const reasoningEffortValue = reasoningEffortOverride || 'auto';
    const updateConfigOverride = (key, value, extraUpdates = {}) => {
        const nextOverrides = upsertConfigOverride(key, value);
        handleFieldChange('codex_exec', { ...codexExec, ...extraUpdates, config: nextOverrides });
    };
    const buildHeadlessArgs = () => {
        const opts = typeof codexExec === 'object' && codexExec ? codexExec : {};
        const args = ['exec'];
        const cd = String(opts.cd || '').trim();
        if (cd) {
            args.push('--cd', cd);
        }
        const color = String(opts.color || '').trim();
        if (['always', 'never', 'auto'].includes(color)) {
            args.push('--color', color);
        }
        if (opts.skip_git_repo_check !== false) {
            args.push('--skip-git-repo-check');
        }
        const sandbox = String(opts.sandbox || '').trim();
        if (sandbox) {
            args.push('--sandbox', sandbox);
        }
        args.push('--model', '{model}');
        const jsonMode = opts.json;
        if (jsonMode !== false) {
            args.push(jsonMode === 'experimental' ? '--experimental-json' : '--json');
        }
        // codex exec no longer supports --ask-for-approval; avoid rendering it in templates.
        if (opts.oss) {
            args.push('--oss');
        }
        const addDirs = opts.add_dirs;
        if (Array.isArray(addDirs)) {
            addDirs.filter(Boolean).forEach((entry) => {
                args.push('--add-dir', String(entry));
            });
        }
        const images = opts.images;
        if (Array.isArray(images)) {
            images.filter(Boolean).forEach((entry) => {
                args.push('--image', String(entry));
            });
        }
        const outputSchema = String(opts.output_schema || '').trim();
        if (outputSchema) {
            args.push('--output-schema', outputSchema);
        }
        const outputLast = String(opts.output_last_message || '').trim();
        if (outputLast) {
            args.push('--output-last-message', outputLast);
        }
        const profile = String(opts.profile || '').trim();
        if (profile) {
            args.push('--profile', profile);
        }
        const configOverrides = opts.config;
        if (Array.isArray(configOverrides)) {
            configOverrides.filter(Boolean).forEach((entry) => {
                args.push('--config', String(entry));
            });
        }
        if (opts.yolo) {
            args.push('--yolo');
        }
        else if (opts.full_auto) {
            args.push('--full-auto');
        }
        args.push('{prompt}');
        return args;
    };
    const headlessTemplate = [provider.command || 'codex', ...buildHeadlessArgs()].join(' ');
    const missingModelPlaceholder = !headlessTemplate.includes('{model}');
    const missingPromptPlaceholder = !headlessTemplate.includes('{prompt}');
    return (_jsxs(BaseProviderSettings, { provider: provider, onUpdate: onUpdate, onValidate: onValidate, children: [_jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "Codex CLI \u914D\u7F6E" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6A21\u578B ID" }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx("input", { type: "text", value: modelId, onChange: (e) => handleFieldChange('model', e.target.value), className: `${cyberInputClasses} flex-1 font-mono`, placeholder: "\u4F8B\u5982 gpt-5.2-codex\u3001o3-mini" }), providerId ? (_jsx(CodexModelBrowser, { providerId: providerId, command: provider.command, tuiArgs: provider.tui_args, env: provider.env, modelId: modelId, onSelect: (value) => handleFieldChange('model', value) })) : (_jsx("button", { type: "button", disabled: true, className: "px-3 py-2 text-[10px] font-semibold border border-white/10 text-text-dim rounded", children: "\u6A21\u578B\u5217\u8868" }))] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u4F7F\u7528 TUI \u67E5\u770B /model \u5217\u8868\u540E\u9009\u62E9\u6A21\u578B ID\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "CLI \u6A21\u5F0F" }), _jsxs("select", { value: cliMode, onChange: (e) => handleFieldChange('cli_mode', e.target.value), className: cyberSelectClasses, children: [_jsx("option", { value: CLI_MODES.HEADLESS, children: "\u9759\u9ED8\u6267\u884C\uFF08exec --json\uFF09" }), _jsx("option", { value: CLI_MODES.TUI, children: "TUI\uFF08\u4EA4\u4E92\uFF09" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u81EA\u52A8\u5316\u5EFA\u8BAE\u4F7F\u7528\u9759\u9ED8\u6267\u884C\uFF1B\u4EBA\u5DE5\u63A2\u67E5\u6A21\u578B\u65F6\u4F7F\u7528\u4EA4\u4E92\u6A21\u5F0F\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u5BA1\u6279\u7B56\u7565" }), _jsxs("select", { value: approvalPolicyValue, onChange: (e) => {
                                    const value = e.target.value;
                                    if (value === 'auto') {
                                        updateConfigOverride('approval_policy', null, { ask_for_approval: '' });
                                        return;
                                    }
                                    updateConfigOverride('approval_policy', `"${value}"`, { ask_for_approval: value });
                                }, className: cyberSelectClasses, children: [_jsx("option", { value: "auto", children: "\u81EA\u52A8\uFF08\u6CBF\u7528 profile\uFF09" }), _jsx("option", { value: "untrusted", children: "\u4E0D\u4FE1\u4EFB\uFF08\u59CB\u7EC8\u8BE2\u95EE\uFF09" }), _jsx("option", { value: "on-failure", children: "\u5931\u8D25\u65F6\u8BE2\u95EE" }), _jsx("option", { value: "on-request", children: "\u6309\u8BF7\u6C42\u8BE2\u95EE" }), _jsx("option", { value: "never", children: "\u4ECE\u4E0D\u8BE2\u95EE" })] }), _jsxs("p", { className: "text-[9px] text-text-dim mt-1", children: ["\u5C06\u5199\u5165 ", _jsx("span", { className: "font-mono", children: "--config approval_policy=..." }), "\uFF08\u8986\u76D6 profile \u9ED8\u8BA4\u503C\uFF09\u3002"] })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u63A8\u7406\u5F3A\u5EA6" }), _jsxs("select", { value: reasoningEffortValue, onChange: (e) => {
                                    const value = e.target.value;
                                    if (value === 'auto') {
                                        updateConfigOverride('model_reasoning_effort', null);
                                        return;
                                    }
                                    updateConfigOverride('model_reasoning_effort', `"${value}"`);
                                }, className: cyberSelectClasses, children: [_jsx("option", { value: "auto", children: "\u81EA\u52A8\uFF08\u6CBF\u7528 profile\uFF09" }), _jsx("option", { value: "low", children: "\u4F4E" }), _jsx("option", { value: "medium", children: "\u4E2D" }), _jsx("option", { value: "high", children: "\u9AD8" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u90E8\u5206\u6A21\u578B\u4EC5\u652F\u6301\u90E8\u5206\u6863\u4F4D\uFF08\u5982 medium/high\uFF09\uFF0C\u81EA\u52A8\u6863\u5C06\u6CBF\u7528 profile \u8BBE\u7F6E\u3002" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u6C99\u7BB1\u7B56\u7565" }), _jsxs("select", { value: codexExecSandbox, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, sandbox: e.target.value }), className: cyberSelectClasses, children: [_jsx("option", { value: "read-only", children: "\u53EA\u8BFB\uFF08\u5B89\u5168\u9ED8\u8BA4\uFF09" }), _jsx("option", { value: "workspace-write", children: "\u5DE5\u4F5C\u533A\u53EF\u5199" }), _jsx("option", { value: "danger-full-access", children: "\u5B8C\u5168\u653E\u5F00\uFF08\u9AD8\u98CE\u9669\uFF09" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u63A7\u5236\u751F\u6210\u547D\u4EE4\u7684\u53EF\u6267\u884C\u8303\u56F4\uFF1B\u53EA\u8BFB\u6700\u5B89\u5168\u3002" })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: codexExec.json !== false, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, json: e.target.checked }), className: "rounded border-white/20 bg-[rgba(35,25,14,0.45)]" }), _jsx("span", { children: "JSON \u6A21\u5F0F" })] }), _jsx("p", { className: "text-[9px] text-text-dim ml-5", children: "\u8F93\u51FA JSON \u4E8B\u4EF6\uFF0C\u4FBF\u4E8E Polaris \u81EA\u52A8\u5904\u7406" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u5F69\u8272\u8F93\u51FA" }), _jsxs("select", { value: codexExecColor, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, color: e.target.value }), className: cyberSelectClasses, children: [_jsx("option", { value: "never", children: "\u4ECE\u4E0D" }), _jsx("option", { value: "auto", children: "\u81EA\u52A8" }), _jsx("option", { value: "always", children: "\u603B\u662F" })] }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u63A7\u5236 ANSI \u989C\u8272\u8F93\u51FA\uFF08JSON \u6A21\u5F0F\u901A\u5E38\u7981\u7528\uFF09" })] }), _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u81EA\u52A8\u5316\u9009\u9879" }), _jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: codexExec.skip_git_repo_check !== false, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, skip_git_repo_check: e.target.checked }), className: "rounded border-white/20 bg-[rgba(35,25,14,0.45)]" }), _jsx("span", { children: "\u8DF3\u8FC7 Git \u4ED3\u5E93\u68C0\u67E5" })] }), _jsx("p", { className: "text-[9px] text-text-dim ml-5", children: "\u5141\u8BB8\u5728\u975E Git \u4ED3\u5E93\u6267\u884C\uFF08\u8BF7\u8C28\u614E\uFF09" })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: codexExec.full_auto === true, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, full_auto: e.target.checked }), className: "rounded border-white/20 bg-[rgba(35,25,14,0.45)]" }), _jsx("span", { children: "\u5168\u81EA\u52A8\u6A21\u5F0F" })] }), _jsx("p", { className: "text-[9px] text-text-dim ml-5", children: "\u4F7F\u7528\u81EA\u52A8\u5316\u9884\u8BBE\uFF08\u5DE5\u4F5C\u533A\u53EF\u5199 + \u6309\u8BF7\u6C42\u5BA1\u6279\uFF09" })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: codexExec.yolo === true, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, yolo: e.target.checked }), className: "rounded border-white/20 bg-[rgba(35,25,14,0.45)]" }), _jsx("span", { children: "YOLO \u6A21\u5F0F\uFF08\u9AD8\u98CE\u9669\uFF09" })] }), _jsx("p", { className: "text-[9px] text-text-dim ml-5 text-red-400", children: "\u26A0\uFE0F \u8DF3\u8FC7\u6240\u6709\u5BA1\u6279\u4E0E\u6C99\u7BB1\u9650\u5236\uFF0C\u4EC5\u5EFA\u8BAE\u5728\u9694\u79BB\u73AF\u5883\u4F7F\u7528\uFF01" })] }), _jsxs("div", { className: "space-y-2", children: [_jsxs("label", { className: "flex items-center gap-2 text-xs text-text-muted", children: [_jsx("input", { type: "checkbox", checked: codexExec.oss === true, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, oss: e.target.checked }), className: "rounded border-white/20 bg-[rgba(35,25,14,0.45)]" }), _jsx("span", { children: "OSS \u63D0\u4F9B\u5546\u6A21\u5F0F" })] }), _jsx("p", { className: "text-[9px] text-text-dim ml-5", children: "\u4F7F\u7528\u672C\u5730 OSS \u63D0\u4F9B\u5546\uFF08\u9700\u672C\u5730 Ollama \u8FD0\u884C\uFF09" })] })] }), cliMode === CLI_MODES.HEADLESS && (_jsxs("div", { className: "bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2", children: [_jsx("h6", { className: "text-xs font-semibold text-text-main", children: "\u9759\u9ED8\u6267\u884C\u6A21\u677F" }), _jsxs("div", { className: "text-[10px] text-text-dim", children: ["\u8BF7\u786E\u4FDD\u5305\u542B ", _jsx("code", { className: "bg-[rgba(35,25,14,0.55)] px-1 rounded", children: '{model}' }), " \u4E0E", ' ', _jsx("code", { className: "bg-[rgba(35,25,14,0.55)] px-1 rounded", children: '{prompt}' }), " \u5360\u4F4D\u7B26\u3002"] }), _jsx("div", { className: "text-[10px] font-mono text-text-main bg-[rgba(35,25,14,0.45)] rounded px-2 py-1 border border-white/10", children: headlessTemplate }), (missingModelPlaceholder || missingPromptPlaceholder) && (_jsxs("div", { className: "text-[10px] text-yellow-300", children: ["\u7F3A\u5931\u5360\u4F4D\u7B26: ", missingModelPlaceholder ? '{model}' : '', missingModelPlaceholder && missingPromptPlaceholder ? ', ' : '', missingPromptPlaceholder ? '{prompt}' : ''] }))] })), cliMode === CLI_MODES.TUI && (_jsxs(_Fragment, { children: [_jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u624B\u52A8\u6A21\u578B\u5F55\u5165" }), _jsx("textarea", { value: provider.manual_models?.join('\n') || '', onChange: (e) => handleFieldChange('manual_models', e.target.value.split('\n').filter((model) => model.trim())), className: "w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16", placeholder: "gpt-4-codex\ngpt-5.2-codex\ncustom-model" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u53EF\u624B\u52A8\u586B\u5199\u6A21\u578B\u540D\uFF1B\u8FD0\u884C `codex` \u540E\u8F93\u5165 `/models` \u53EF\u67E5\u770B\u53EF\u7528\u6A21\u578B\u3002" })] }), _jsxs("div", { className: "bg-[rgba(35,25,14,0.3)] rounded p-3 space-y-2", children: [_jsx("h6", { className: "text-xs font-semibold text-text-main", children: "TUI \u4F7F\u7528\u8BF4\u660E" }), _jsxs("div", { className: "text-[10px] text-text-dim space-y-1", children: [_jsxs("p", { children: [_jsx("span", { className: "text-text-muted", children: "\u6A21\u578B\u53D1\u73B0:" }), " \u8FD0\u884C `codex` \u2192 \u8F93\u5165 `/models`"] }), _jsxs("p", { children: [_jsx("span", { className: "text-text-muted", children: "\u4F1A\u8BDD\u72B6\u6001:" }), " \u8FD0\u884C `codex` \u2192 \u8F93\u5165 `/status`"] }), _jsxs("p", { children: [_jsx("span", { className: "text-text-muted", children: "\u6743\u9650\u8BBE\u7F6E:" }), " \u8FD0\u884C `codex` \u2192 \u8F93\u5165 `/permissions`"] }), _jsxs("p", { children: [_jsx("span", { className: "text-text-muted", children: "\u5E2E\u52A9\u6587\u6863:" }), " \u8FD0\u884C `codex` \u2192 \u8F93\u5165 `/help`"] })] })] })] })), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "Profile \u6863\u6848" }), _jsx("input", { type: "text", value: codexExecProfile, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, profile: e.target.value }), className: cyberInputClasses, placeholder: "\u9ED8\u8BA4\uFF08default\uFF09\u3001codex \u6216\u81EA\u5B9A\u4E49\u6863\u6848\u540D" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u4ECE `~/.codex/config.toml` \u52A0\u8F7D\u9884\u8BBE profile" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u914D\u7F6E\u8986\u76D6\uFF08key=value\uFF09" }), _jsx("textarea", { value: codexExecConfig.join('\n'), onChange: (e) => handleFieldChange('codex_exec', {
                                    ...codexExec,
                                    config: e.target.value.split('\n').filter(config => config.trim() && config.includes('='))
                                }), className: "w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16", placeholder: "key1=value1\nkey2=value2" }), _jsxs("p", { className: "text-[9px] text-text-dim mt-1", children: ["\u652F\u6301 TOML \u503C\u3002\u793A\u4F8B\uFF1A", _jsx("span", { className: "font-mono", children: "web_search=\"live\"" }), "\u3001", ' ', _jsx("span", { className: "font-mono", children: "shell_environment_policy.include_only=[\"PATH\",\"HOME\"]" })] })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u9644\u52A0\u76EE\u5F55\u6388\u6743" }), _jsx("textarea", { value: codexExecAddDirs.join('\n'), onChange: (e) => handleFieldChange('codex_exec', {
                                    ...codexExec,
                                    add_dirs: e.target.value.split('\n').filter(dir => dir.trim())
                                }), className: "w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16", placeholder: "/path/to/dir1\n/path/to/dir2" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u4E3A\u5DE5\u4F5C\u533A\u5916\u76EE\u5F55\u6388\u4E88\u5199\u6743\u9650" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u8F93\u51FA Schema" }), _jsx("input", { type: "text", value: codexExecOutputSchema, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, output_schema: e.target.value }), className: `${cyberInputClasses} font-mono`, placeholder: "/path/to/schema.json" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u7528\u4E8E\u6821\u9A8C\u6700\u7EC8\u8F93\u51FA\u7684 JSON Schema \u6587\u4EF6\uFF08\u4FBF\u4E8E\u6D41\u6C34\u7EBF\uFF09" })] }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u672B\u6761\u6D88\u606F\u8F93\u51FA\u8DEF\u5F84" }), _jsx("input", { type: "text", value: codexExecOutputLastMessage, onChange: (e) => handleFieldChange('codex_exec', { ...codexExec, output_last_message: e.target.value }), className: `${cyberInputClasses} font-mono`, placeholder: "runtime/CODEX_LAST_MESSAGE.md" }), _jsx("p", { className: "text-[9px] text-text-dim mt-1", children: "\u5C06\u6700\u540E\u4E00\u6761\u52A9\u624B\u6D88\u606F\u5199\u5165\u6587\u4EF6\uFF0C\u4FBF\u4E8E\u811A\u672C\u7EED\u8DD1" })] })] }), _jsxs("div", { className: "space-y-3", children: [_jsx("h5", { className: "text-xs font-semibold text-text-main", children: "\u73AF\u5883\u53D8\u91CF" }), _jsxs("div", { children: [_jsx("label", { className: "block text-xs text-text-muted mb-1", children: "\u73AF\u5883\u53D8\u91CF\uFF08JSON\uFF09" }), _jsx("textarea", { value: JSON.stringify(provider.env || {}, null, 2), onChange: (e) => {
                                    try {
                                        const env = JSON.parse(e.target.value);
                                        handleFieldChange('env', env);
                                    }
                                    catch {
                                        // Invalid JSON, don't update
                                    }
                                }, className: "w-full bg-[rgba(35,25,14,0.45)] text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-20", placeholder: '{"KEY": "value"}' }), _jsxs("p", { className: "text-[9px] text-text-dim mt-1", children: ["\u4F7F\u7528 JSON \u4F20\u5165\u73AF\u5883\u53D8\u91CF\u3002\u53EF\u5F15\u7528 keychain \u503C\uFF0C\u4F8B\u5982", _jsx("span", { className: "font-mono", children: " keychain:llm:minimax " }), "\uFF08\u6216 ", _jsxs("span", { className: "font-mono", children: ["$", '{', "keychain:llm:minimax", '}'] }), "\uFF09\uFF0C\u8FD0\u884C\u65F6\u5C06\u81EA\u52A8\u89E3\u6790\u3002"] })] })] })] }));
}
