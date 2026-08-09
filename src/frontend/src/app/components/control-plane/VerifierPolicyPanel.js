import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Eye, Globe2, Loader2, Save, ShieldCheck, TerminalSquare, TriangleAlert } from 'lucide-react';
import { getVerifierPolicy, updateVerifierPolicy, } from '@/services/controlPlane';
const CAPABILITIES = [
    { key: 'browser', label: 'Browser 验收', icon: Globe2 },
    { key: 'visual', label: '视觉验收', icon: Eye },
    { key: 'llm_judge', label: '多模态 QA', icon: ShieldCheck },
    { key: 'custom_script', label: '用户脚本', icon: TerminalSquare },
];
function buildDraft(policy) {
    return {
        enabled: {
            browser: Boolean(policy?.capabilities.browser.enabled),
            visual: Boolean(policy?.capabilities.visual.enabled),
            llm_judge: Boolean(policy?.capabilities.llm_judge.enabled),
            custom_script: Boolean(policy?.capabilities.custom_script.enabled),
        },
        required: {
            browser: Boolean(policy?.capabilities.browser.required),
            visual: Boolean(policy?.capabilities.visual.required),
            llm_judge: Boolean(policy?.capabilities.llm_judge.required),
            custom_script: Boolean(policy?.capabilities.custom_script.required),
        },
        customScripts: policy?.custom_scripts ?? [],
    };
}
function requiredModalities(draft) {
    return CAPABILITIES
        .filter((item) => draft.enabled[item.key] && draft.required[item.key])
        .map((item) => item.key);
}
function scriptId(path) {
    const leaf = path.split(/[\\/]/).pop() || 'custom-script';
    return leaf.replace(/\.[^.]+$/, '') || 'custom-script';
}
export function VerifierPolicyPanel() {
    const [policy, setPolicy] = useState(null);
    const [draft, setDraft] = useState(() => buildDraft(null));
    const [scriptPath, setScriptPath] = useState('');
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const loadPolicy = useCallback(async () => {
        setLoading(true);
        setError(null);
        const result = await getVerifierPolicy();
        if (result.ok && result.data) {
            setPolicy(result.data);
            setDraft(buildDraft(result.data));
        }
        else {
            setError(result.error || '读取验收策略失败');
        }
        setLoading(false);
    }, []);
    useEffect(() => {
        void loadPolicy();
    }, [loadPolicy]);
    const dirty = useMemo(() => {
        if (!policy)
            return false;
        const current = buildDraft(policy);
        return JSON.stringify(current) !== JSON.stringify(draft);
    }, [draft, policy]);
    const toggleEnabled = (key, value) => {
        setDraft((prev) => ({
            ...prev,
            enabled: { ...prev.enabled, [key]: value },
            required: { ...prev.required, [key]: value ? prev.required[key] : false },
        }));
    };
    const toggleRequired = (key, value) => {
        setDraft((prev) => ({
            ...prev,
            required: { ...prev.required, [key]: value },
        }));
    };
    const addScript = () => {
        const normalized = scriptPath.trim().replace(/\\/g, '/').replace(/^\.?\//, '');
        if (!normalized)
            return;
        setDraft((prev) => ({
            ...prev,
            enabled: { ...prev.enabled, custom_script: true },
            customScripts: [
                ...prev.customScripts,
                {
                    id: scriptId(normalized),
                    path: normalized,
                    modality: 'custom_script',
                    enabled: true,
                    required: false,
                },
            ],
        }));
        setScriptPath('');
    };
    const removeScript = (index) => {
        setDraft((prev) => ({
            ...prev,
            customScripts: prev.customScripts.filter((_, itemIndex) => itemIndex !== index),
        }));
    };
    const savePolicy = async () => {
        setSaving(true);
        setError(null);
        const result = await updateVerifierPolicy({
            browser_enabled: draft.enabled.browser,
            visual_enabled: draft.enabled.visual,
            llm_judge_enabled: draft.enabled.llm_judge,
            custom_script_enabled: draft.enabled.custom_script,
            required_modalities: requiredModalities(draft),
            custom_scripts: draft.customScripts,
        });
        if (result.ok && result.data) {
            setPolicy(result.data);
            setDraft(buildDraft(result.data));
        }
        else {
            setError(result.error || '保存验收策略失败');
        }
        setSaving(false);
    };
    return (_jsxs("section", { className: "soft-panel-subtle rounded-xl border border-cyan-400/15 p-4", children: [_jsxs("div", { className: "mb-4 flex items-start justify-between gap-3", children: [_jsxs("div", { children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-text-main", children: [_jsx(ShieldCheck, { className: "size-4 text-cyan-300" }), "\u5E73\u53F0\u9A8C\u6536\u7B56\u7565"] }), _jsx("p", { className: "mt-1 text-[11px] leading-5 text-text-dim", children: "Browser\u3001\u89C6\u89C9\u3001\u591A\u6A21\u6001 QA \u548C\u7528\u6237\u811A\u672C\u90FD\u662F\u53EF\u9009\u9A8C\u6536\u80FD\u529B\u3002\u672A\u542F\u7528\u65F6\u4E0D\u4F1A\u963B\u585E\u6B63\u5F0F\u9879\u76EE\uFF0C\u4E5F\u4E0D\u4F1A\u7531\u5185\u90E8\u6D4B\u8BD5\u8BBE\u65BD\u51B3\u5B9A\u3002" })] }), _jsxs("button", { type: "button", onClick: savePolicy, disabled: saving || loading || !dirty, className: "inline-flex items-center gap-2 rounded-lg border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-[11px] font-semibold text-cyan-100 transition hover:bg-cyan-400/15 disabled:cursor-not-allowed disabled:opacity-50", children: [saving ? _jsx(Loader2, { className: "size-3 animate-spin" }) : _jsx(Save, { className: "size-3" }), "\u4FDD\u5B58\u7B56\u7565"] })] }), error ? (_jsxs("div", { className: "mb-3 flex items-start gap-2 rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-[11px] text-red-100", children: [_jsx(TriangleAlert, { className: "mt-0.5 size-3 shrink-0" }), _jsx("span", { children: error })] })) : null, _jsx("div", { className: "grid gap-3 lg:grid-cols-2", children: CAPABILITIES.map(({ key, label, icon: Icon }) => {
                    const status = policy?.capabilities[key];
                    const canRequire = draft.enabled[key] && Boolean(status?.available || draft.required[key]);
                    return (_jsxs("div", { className: "rounded-lg border border-white/10 bg-black/20 p-3", children: [_jsxs("div", { className: "flex items-center justify-between gap-3", children: [_jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Icon, { className: "size-4 text-cyan-200" }), _jsxs("div", { children: [_jsx("div", { className: "text-xs font-semibold text-text-main", children: label }), _jsxs("div", { className: "mt-0.5 flex items-center gap-1 text-[10px] text-text-dim", children: [status?.available ? (_jsx(CheckCircle2, { className: "size-3 text-emerald-300" })) : (_jsx(TriangleAlert, { className: "size-3 text-amber-300" })), _jsx("span", { children: status?.available ? '环境可用' : '环境未声明' })] })] })] }), _jsxs("label", { className: "flex items-center gap-2 text-[11px] text-text-muted", children: [_jsx("input", { type: "checkbox", checked: draft.enabled[key], onChange: (event) => toggleEnabled(key, event.target.checked), className: "size-4 rounded border-white/20 bg-black/30 text-cyan-300 focus:ring-cyan-300/40" }), "\u542F\u7528"] })] }), _jsxs("label", { className: "mt-3 flex items-center gap-2 text-[11px] text-text-muted", children: [_jsx("input", { type: "checkbox", checked: draft.required[key], disabled: !canRequire, onChange: (event) => toggleRequired(key, event.target.checked), className: "size-4 rounded border-white/20 bg-black/30 text-cyan-300 focus:ring-cyan-300/40 disabled:opacity-40" }), "\u8BBE\u4E3A\u5FC5\u9700\u8BC1\u636E"] }), draft.enabled[key] && !status?.available ? (_jsx("p", { className: "mt-2 text-[10px] leading-4 text-amber-100/75", children: "\u5F53\u524D\u73AF\u5883\u672A\u58F0\u660E\u8BE5\u80FD\u529B\uFF1B\u53EF\u4EE5\u4FDD\u7559\u542F\u7528\u610F\u56FE\uFF0C\u4F46\u4E0D\u80FD\u65B0\u589E\u4E3A\u5FC5\u9700\u8BC1\u636E\u3002" })) : null, !status?.available && status?.reason ? (_jsx("p", { className: "mt-2 text-[10px] leading-4 text-amber-100/75", children: status.reason })) : null] }, key));
                }) }), _jsxs("div", { className: "mt-4 rounded-lg border border-white/10 bg-black/20 p-3", children: [_jsx("div", { className: "mb-2 text-xs font-semibold text-text-main", children: "\u81EA\u5B9A\u4E49\u811A\u672C" }), _jsxs("div", { className: "flex flex-col gap-2 sm:flex-row", children: [_jsx("input", { value: scriptPath, onChange: (event) => setScriptPath(event.target.value), placeholder: "tests/physics_verifier.py", className: "min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-xs text-text-main outline-none focus:border-cyan-300/60" }), _jsx("button", { type: "button", onClick: addScript, className: "rounded-lg border border-white/10 px-3 py-2 text-[11px] text-text-muted transition hover:border-cyan-300/40 hover:text-cyan-100", children: "\u6DFB\u52A0" })] }), draft.customScripts.length > 0 ? (_jsx("div", { className: "mt-3 space-y-2", children: draft.customScripts.map((script, index) => (_jsxs("div", { className: "flex items-center justify-between gap-3 rounded-md bg-white/[0.04] px-3 py-2", children: [_jsxs("div", { className: "min-w-0", children: [_jsx("div", { className: "truncate text-[11px] font-medium text-text-main", children: script.path }), _jsx("div", { className: "text-[10px] text-text-dim", children: script.modality })] }), _jsx("button", { type: "button", onClick: () => removeScript(index), className: "shrink-0 text-[10px] text-text-dim hover:text-red-200", children: "\u79FB\u9664" })] }, `${script.path}-${index}`))) })) : null] })] }));
}
