import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * Workflow Settings Tab
 *
 * Configuration for workflow execution, task management, and
 * runtime behavior settings.
 */
import { useState, useEffect } from 'react';
import { Workflow, Clock, Cpu, Activity, RotateCcw, AlertCircle, CheckCircle2, Loader2, Layers, Terminal, Sparkles, Globe, } from 'lucide-react';
import { Input } from '@/app/components/ui/input';
import { Label } from '@/app/components/ui/label';
import { Switch } from '@/app/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '@/app/components/ui/select';
import { cn } from '@/app/components/ui/utils';
// Section Card Component
function SectionCard({ children, className, title, icon: Icon, description, }) {
    return (_jsxs("div", { className: cn('rounded-lg border border-slate-700/60 bg-slate-800/70 shadow-sm', className), children: [_jsxs("div", { className: "flex items-center gap-3 px-5 py-3 border-b border-slate-700/50", children: [_jsx("div", { className: "flex items-center justify-center w-8 h-8 rounded-md bg-slate-700/60", children: _jsx(Icon, { className: "w-4 h-4 text-slate-400" }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("h3", { className: "text-sm font-medium text-slate-200", children: title }), description && _jsx("p", { className: "text-xs text-slate-500 mt-0.5", children: description })] })] }), _jsx("div", { className: "p-5", children: children })] }));
}
// Form Field Component
function FormField({ label, children, className, hint, }) {
    return (_jsxs("div", { className: cn('space-y-2', className), children: [_jsx(Label, { className: "text-xs font-medium text-slate-300 uppercase tracking-wider", children: label }), children, hint && (_jsxs("p", { className: "text-xs text-slate-500 flex items-center gap-1", children: [_jsx(AlertCircle, { className: "w-3 h-3" }), hint] }))] }));
}
// Number Input Component
function NumberInput({ value, onChange, min, max, suffix, }) {
    return (_jsxs("div", { className: "relative", children: [_jsx(Input, { type: "number", value: value, onChange: (e) => onChange(Number(e.target.value)), min: min, max: max, className: cn('h-10 bg-slate-950/50 border-slate-700/50 text-slate-100', 'focus:border-slate-500/50 focus:ring-slate-500/20', 'placeholder:text-slate-600', suffix && 'pr-12') }), suffix && (_jsx("span", { className: "absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500", children: suffix }))] }));
}
// Toggle Field Component
function ToggleField({ label, description, checked, onChange, icon: Icon, }) {
    return (_jsxs("div", { className: "flex items-start gap-4 p-4 rounded-lg bg-slate-900/50 border border-slate-700/40 transition-colors", children: [_jsx("div", { className: "flex items-center justify-center w-9 h-9 rounded-md bg-slate-800/70 shrink-0", children: _jsx(Icon, { className: "w-5 h-5 text-slate-400" }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center justify-between gap-4", children: [_jsx("span", { className: "text-sm font-medium text-slate-200", children: label }), _jsx(Switch, { "aria-label": label, checked: checked, onCheckedChange: onChange, className: "data-[state=checked]:bg-emerald-500" })] }), description && _jsx("p", { className: "text-xs text-slate-500 mt-1", children: description })] })] }));
}
export function WorkflowSettingsTab({ settings, onSave }) {
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState(null);
    const [formState, setFormState] = useState({
        directorIterations: 1,
        directorExecutionMode: 'parallel',
        directorMaxParallelTasks: 3,
        directorReadyTimeoutSeconds: 30,
        directorClaimTimeoutSeconds: 30,
        directorPhaseTimeoutSeconds: 900,
        directorCompleteTimeoutSeconds: 30,
        directorTaskTimeoutSeconds: 3600,
        directorForever: false,
        directorShowOutput: true,
        pmRunsDirector: true,
        pmDirectorShowOutput: true,
        pmDirectorTimeout: 600,
        pmDirectorIterations: 1,
        pmDirectorMatchMode: 'latest',
        pmMaxFailures: 5,
        pmMaxBlocked: 5,
        pmMaxSame: 3,
        qaEnabled: true,
        slmEnabled: false,
        verifierBrowserEnabled: false,
        verifierVisualEnabled: false,
        verifierMultimodalLlmEnabled: false,
        verifierUserScriptsEnabled: false,
        verifierDomainEnabled: false,
    });
    useEffect(() => {
        if (!settings)
            return;
        setFormState((prev) => ({
            ...prev,
            directorIterations: settings.director_iterations ?? prev.directorIterations,
            directorExecutionMode: settings.director_execution_mode ?? prev.directorExecutionMode,
            directorMaxParallelTasks: settings.director_max_parallel_tasks ?? prev.directorMaxParallelTasks,
            directorReadyTimeoutSeconds: settings.director_ready_timeout_seconds ?? prev.directorReadyTimeoutSeconds,
            directorClaimTimeoutSeconds: settings.director_claim_timeout_seconds ?? prev.directorClaimTimeoutSeconds,
            directorPhaseTimeoutSeconds: settings.director_phase_timeout_seconds ?? prev.directorPhaseTimeoutSeconds,
            directorCompleteTimeoutSeconds: settings.director_complete_timeout_seconds ?? prev.directorCompleteTimeoutSeconds,
            directorTaskTimeoutSeconds: settings.director_task_timeout_seconds ?? prev.directorTaskTimeoutSeconds,
            directorForever: settings.director_forever ?? prev.directorForever,
            directorShowOutput: settings.director_show_output ?? prev.directorShowOutput,
            pmRunsDirector: settings.pm_runs_director ?? prev.pmRunsDirector,
            pmDirectorShowOutput: settings.pm_director_show_output ?? prev.pmDirectorShowOutput,
            pmDirectorTimeout: settings.pm_director_timeout ?? prev.pmDirectorTimeout,
            pmDirectorIterations: settings.pm_director_iterations ?? prev.pmDirectorIterations,
            pmDirectorMatchMode: settings.pm_director_match_mode ?? prev.pmDirectorMatchMode,
            pmMaxFailures: settings.pm_max_failures ?? prev.pmMaxFailures,
            pmMaxBlocked: settings.pm_max_blocked ?? prev.pmMaxBlocked,
            pmMaxSame: settings.pm_max_same ?? prev.pmMaxSame,
            qaEnabled: settings.qa_enabled ?? prev.qaEnabled,
            slmEnabled: settings.slm_enabled ?? prev.slmEnabled,
            verifierBrowserEnabled: settings.verifier_policy?.browser_enabled ?? prev.verifierBrowserEnabled,
            verifierVisualEnabled: settings.verifier_policy?.visual_enabled ?? prev.verifierVisualEnabled,
            verifierMultimodalLlmEnabled: settings.verifier_policy?.multimodal_llm_enabled ?? prev.verifierMultimodalLlmEnabled,
            verifierUserScriptsEnabled: settings.verifier_policy?.user_scripts_enabled ?? prev.verifierUserScriptsEnabled,
            verifierDomainEnabled: settings.verifier_policy?.domain_verifiers_enabled ?? prev.verifierDomainEnabled,
        }));
    }, [settings]);
    const handleSave = async () => {
        setSaving(true);
        setError(null);
        try {
            await onSave({
                director_iterations: formState.directorIterations,
                director_execution_mode: formState.directorExecutionMode,
                director_max_parallel_tasks: formState.directorMaxParallelTasks,
                director_ready_timeout_seconds: formState.directorReadyTimeoutSeconds,
                director_claim_timeout_seconds: formState.directorClaimTimeoutSeconds,
                director_phase_timeout_seconds: formState.directorPhaseTimeoutSeconds,
                director_complete_timeout_seconds: formState.directorCompleteTimeoutSeconds,
                director_task_timeout_seconds: formState.directorTaskTimeoutSeconds,
                director_forever: formState.directorForever,
                director_show_output: formState.directorShowOutput,
                pm_runs_director: formState.pmRunsDirector,
                pm_director_show_output: formState.pmDirectorShowOutput,
                pm_director_timeout: formState.pmDirectorTimeout,
                pm_director_iterations: formState.pmDirectorIterations,
                pm_director_match_mode: formState.pmDirectorMatchMode,
                pm_max_failures: formState.pmMaxFailures,
                pm_max_blocked: formState.pmMaxBlocked,
                pm_max_same: formState.pmMaxSame,
                qa_enabled: formState.qaEnabled,
                slm_enabled: formState.slmEnabled,
                verifier_policy: {
                    browser_enabled: formState.verifierBrowserEnabled,
                    visual_enabled: formState.verifierVisualEnabled,
                    multimodal_llm_enabled: formState.verifierMultimodalLlmEnabled,
                    user_scripts_enabled: formState.verifierUserScriptsEnabled,
                    domain_verifiers_enabled: formState.verifierDomainEnabled,
                    required_evidence_modalities: settings?.verifier_policy?.required_evidence_modalities ?? [],
                },
            });
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        }
        catch (err) {
            setError(err instanceof Error ? err.message : '保存失败');
        }
        finally {
            setSaving(false);
        }
    };
    const updateField = (field, value) => {
        setFormState((prev) => ({ ...prev, [field]: value }));
    };
    return (_jsxs("div", { className: "space-y-6 pb-20", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-xl font-bold text-slate-100 flex items-center gap-2", children: [_jsx(Workflow, { className: "w-6 h-6 text-slate-400" }), "\u5DE5\u4F5C\u6D41\u8BBE\u7F6E"] }), _jsx("p", { className: "text-sm text-slate-400 mt-1", children: "\u914D\u7F6E Director \u6267\u884C\u5668\u3001PM \u8C03\u5EA6\u5668\u4E0E\u5DE5\u4F5C\u6D41\u884C\u4E3A" })] }), _jsx("button", { onClick: handleSave, disabled: saving, className: cn('flex items-center gap-2 px-5 py-2 rounded-lg font-medium text-sm', 'transition-colors duration-150', saved
                            ? 'bg-emerald-500/[0.15] text-emerald-400 border border-emerald-500/30'
                            : 'bg-emerald-600 text-white hover:bg-emerald-700'), children: saving ? (_jsxs(_Fragment, { children: [_jsx(Loader2, { className: "w-4 h-4 animate-spin" }), "\u4FDD\u5B58\u4E2D..."] })) : saved ? (_jsxs(_Fragment, { children: [_jsx(CheckCircle2, { className: "w-4 h-4" }), "\u5DF2\u4FDD\u5B58"] })) : (_jsxs(_Fragment, { children: [_jsx(Sparkles, { className: "w-4 h-4" }), "\u4FDD\u5B58\u8BBE\u7F6E"] })) })] }), error && (_jsxs("div", { className: "flex items-center gap-2 p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400", children: [_jsx(AlertCircle, { className: "w-5 h-5 shrink-0" }), _jsx("span", { className: "text-sm", children: error })] })), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Cpu, { className: "w-4 h-4 text-slate-400" }), "Director \u6267\u884C\u5668\u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4", children: [_jsx(SectionCard, { title: "\u6267\u884C\u6A21\u5F0F", icon: Activity, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u6267\u884C\u6A21\u5F0F", children: _jsxs(Select, { value: formState.directorExecutionMode, onValueChange: (v) => updateField('directorExecutionMode', v), children: [_jsx(SelectTrigger, { className: "bg-slate-950/50 border-slate-700/50", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { className: "bg-slate-900 border-slate-700", children: [_jsx(SelectItem, { value: "serial", children: "\u4E32\u884C (Serial)" }), _jsx(SelectItem, { value: "parallel", children: "\u5E76\u884C (Parallel)" })] })] }) }), formState.directorExecutionMode === 'parallel' && (_jsx(FormField, { label: "\u6700\u5927\u5E76\u884C\u4EFB\u52A1", children: _jsx(NumberInput, { value: formState.directorMaxParallelTasks, onChange: (v) => updateField('directorMaxParallelTasks', v), min: 1, max: 10, suffix: "\u4E2A" }) })), _jsx(ToggleField, { label: "\u65E0\u9650\u5FAA\u73AF\u6A21\u5F0F", description: "\u6301\u7EED\u6267\u884C\u4E0D\u505C\u6B62", checked: formState.directorForever, onChange: (v) => updateField('directorForever', v), icon: RotateCcw }), _jsx(ToggleField, { label: "\u663E\u793A\u8F93\u51FA", description: "\u663E\u793A\u6267\u884C\u65E5\u5FD7", checked: formState.directorShowOutput, onChange: (v) => updateField('directorShowOutput', v), icon: Terminal })] }) }), _jsx(SectionCard, { title: "\u8D85\u65F6\u914D\u7F6E", icon: Clock, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u5C31\u7EEA\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorReadyTimeoutSeconds, onChange: (v) => updateField('directorReadyTimeoutSeconds', v), min: 5, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8BA4\u9886\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorClaimTimeoutSeconds, onChange: (v) => updateField('directorClaimTimeoutSeconds', v), min: 5, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u9636\u6BB5\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorPhaseTimeoutSeconds, onChange: (v) => updateField('directorPhaseTimeoutSeconds', v), min: 60, suffix: "\u79D2" }) })] }) }), _jsx(SectionCard, { title: "\u4EFB\u52A1\u8D85\u65F6", icon: AlertCircle, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u5B8C\u6210\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorCompleteTimeoutSeconds, onChange: (v) => updateField('directorCompleteTimeoutSeconds', v), min: 10, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u4EFB\u52A1\u8D85\u65F6", hint: "\u5355\u4E2A\u4EFB\u52A1\u6700\u5927\u6267\u884C\u65F6\u95F4", children: _jsx(NumberInput, { value: formState.directorTaskTimeoutSeconds, onChange: (v) => updateField('directorTaskTimeoutSeconds', v), min: 300, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8FED\u4EE3\u6B21\u6570", children: _jsx(NumberInput, { value: formState.directorIterations, onChange: (v) => updateField('directorIterations', v), min: 1, max: 100, suffix: "\u6B21" }) })] }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Layers, { className: "w-4 h-4 text-emerald-400" }), "PM \u96C6\u6210\u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4", children: [_jsx(SectionCard, { title: "\u5168\u94FE\u8DEF\u6267\u884C", icon: Layers, children: _jsxs("div", { className: "space-y-3", children: [_jsx(ToggleField, { label: "\u542F\u7528\u6267\u884C\u9636\u6BB5", description: "PM\u3001Chief Engineer\u3001Director \u987A\u5E8F\u6267\u884C", checked: formState.pmRunsDirector, onChange: (v) => updateField('pmRunsDirector', v), icon: Cpu }), _jsx(ToggleField, { label: "\u663E\u793A\u8F93\u51FA", description: "\u5728\u7EC8\u7AEF\u663E\u793A Director \u8F93\u51FA", checked: formState.pmDirectorShowOutput, onChange: (v) => updateField('pmDirectorShowOutput', v), icon: Terminal }), _jsx(FormField, { label: "\u8D85\u65F6\u65F6\u95F4", className: "mt-4", children: _jsx(NumberInput, { value: formState.pmDirectorTimeout, onChange: (v) => updateField('pmDirectorTimeout', v), min: 60, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8FED\u4EE3\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmDirectorIterations, onChange: (v) => updateField('pmDirectorIterations', v), min: 1, max: 100, suffix: "\u6B21" }) })] }) }), _jsx(SectionCard, { title: "\u6545\u969C\u6062\u590D", icon: RotateCcw, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u6700\u5927\u5931\u8D25\u6B21\u6570", hint: "\u8D85\u8FC7\u540E\u6682\u505C\u4EFB\u52A1", children: _jsx(NumberInput, { value: formState.pmMaxFailures, onChange: (v) => updateField('pmMaxFailures', v), min: 1, max: 20, suffix: "\u6B21" }) }), _jsx(FormField, { label: "\u6700\u5927\u963B\u585E\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmMaxBlocked, onChange: (v) => updateField('pmMaxBlocked', v), min: 1, max: 20, suffix: "\u6B21" }) }), _jsx(FormField, { label: "\u6700\u5927\u91CD\u590D\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmMaxSame, onChange: (v) => updateField('pmMaxSame', v), min: 1, max: 10, suffix: "\u6B21" }) })] }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(CheckCircle2, { className: "w-4 h-4 text-cyan-300" }), "\u9A8C\u6536\u80FD\u529B\u7B56\u7565"] }), _jsxs(SectionCard, { title: "\u53EF\u9009\u8BC1\u636E\u6A21\u6001", icon: Activity, description: "\u8FD9\u4E9B\u80FD\u529B\u53EA\u5728\u542F\u7528\u540E\u53C2\u4E0E\u5E73\u53F0\u7EA7 QA/Verifier\uFF1B\u672A\u542F\u7528\u65F6\u4E0D\u4F1A\u6210\u4E3A\u786C\u95E8\u7981", children: [_jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-3", children: [_jsx(ToggleField, { label: "Browser \u9A8C\u6536", description: "\u4F7F\u7528\u6D4F\u89C8\u5668/Playwright \u7C7B\u73AF\u5883\u91C7\u96C6\u9875\u9762\u8FD0\u884C\u8BC1\u636E", checked: formState.verifierBrowserEnabled, onChange: (v) => updateField('verifierBrowserEnabled', v), icon: Globe }), _jsx(ToggleField, { label: "Visual \u9A8C\u6536", description: "\u91C7\u96C6\u622A\u56FE\u3001Canvas \u6216\u89C6\u89C9\u72B6\u6001\u8BC1\u636E\uFF1B\u53EF\u4F9B\u591A\u6A21\u6001 QA \u4F7F\u7528", checked: formState.verifierVisualEnabled, onChange: (v) => updateField('verifierVisualEnabled', v), icon: Sparkles }), _jsx(ToggleField, { label: "\u591A\u6A21\u6001 LLM \u88C1\u5224", description: "\u5141\u8BB8\u652F\u6301\u89C6\u89C9\u7684 QA \u6A21\u578B\u53C2\u4E0E\u9875\u9762\u3001\u56FE\u50CF\u6216\u4EA4\u4E92\u7ED3\u679C\u9A8C\u6536", checked: formState.verifierMultimodalLlmEnabled, onChange: (v) => updateField('verifierMultimodalLlmEnabled', v), icon: Cpu }), _jsx(ToggleField, { label: "\u7528\u6237\u811A\u672C\u9A8C\u8BC1", description: "\u5141\u8BB8\u9879\u76EE\u63D0\u4F9B\u53D7\u63A7\u811A\u672C\u9A8C\u8BC1\u7269\u7406\u3001\u7B97\u6CD5\u3001\u6570\u636E\u6216\u9886\u57DF\u89C4\u5219", checked: formState.verifierUserScriptsEnabled, onChange: (v) => updateField('verifierUserScriptsEnabled', v), icon: Terminal }), _jsx(ToggleField, { label: "\u9886\u57DF\u9A8C\u8BC1\u5668", description: "\u542F\u7528\u7269\u7406\u5F15\u64CE\u3001\u7C92\u5B50\u3001\u6570\u636E\u8D28\u91CF\u3001\u7B97\u6CD5\u9608\u503C\u7B49\u4E13\u7528\u9A8C\u8BC1\u5668", checked: formState.verifierDomainEnabled, onChange: (v) => updateField('verifierDomainEnabled', v), icon: Layers })] }), _jsx("div", { className: "mt-4 rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-3 text-xs text-cyan-100/80", children: "Run Ledger \u53EA\u628A\u663E\u5F0F required \u7684\u8BC1\u636E\u6A21\u6001\u89C6\u4E3A\u786C\u95E8\u7981\uFF1B\u8FD9\u4E9B\u5F00\u5173\u8868\u793A\u80FD\u529B\u662F\u5426\u53EF\u7528\uFF0C\u4E0D\u4F1A\u9690\u5F0F\u8981\u6C42\u7528\u6237\u5B89\u88C5\u6D4F\u89C8\u5668\u6216\u89C6\u89C9\u73AF\u5883\u3002" })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Sparkles, { className: "w-4 h-4 text-slate-400" }), "\u529F\u80FD\u5F00\u5173"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4", children: [_jsx(ToggleField, { label: "QA \u5BA1\u67E5", description: "\u542F\u7528\u8D28\u91CF\u5BA1\u67E5\u6D41\u7A0B", checked: formState.qaEnabled, onChange: (v) => updateField('qaEnabled', v), icon: CheckCircle2 }), _jsx(ToggleField, { label: "SLM \u6A21\u5F0F", description: "\u542F\u7528\u5C0F\u578B\u8BED\u8A00\u6A21\u578B\u4F18\u5316", checked: formState.slmEnabled, onChange: (v) => updateField('slmEnabled', v), icon: Cpu })] })] })] }));
}
