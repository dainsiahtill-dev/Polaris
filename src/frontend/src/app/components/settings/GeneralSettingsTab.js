import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
/**
 * GeneralSettingsTab - 通用设置标签页
 * 采用 Glassmorphism 设计风格，支持端口/API Key/LLM系统配置
 */
import { useState, useEffect } from 'react';
import { Settings, Clock, Zap, HardDrive, FileText, Server, RotateCcw, AlertCircle, CheckCircle2, Loader2, Cpu, MemoryStick, Terminal, Activity, Globe, Layers, Sparkles, Bug, Palette, Sun, Moon, Monitor, } from 'lucide-react';
import { Input } from '@/app/components/ui/input';
import { Label } from '@/app/components/ui/label';
import { Switch } from '@/app/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '@/app/components/ui/select';
import { cn } from '@/app/components/ui/utils';
import { useTheme } from '@/app/hooks/useTheme';
function SectionCard({ children, className, title, icon: Icon, description, }) {
    return (_jsxs("div", { className: cn('rounded-lg border border-slate-700/60 bg-slate-800/70 shadow-sm', className), children: [_jsxs("div", { className: "flex items-center gap-3 px-5 py-3 border-b border-slate-700/50", children: [_jsx("div", { className: "flex items-center justify-center w-8 h-8 rounded-md bg-slate-700/60", children: _jsx(Icon, { className: "w-4 h-4 text-slate-400" }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsx("h3", { className: "text-sm font-medium text-slate-200", children: title }), description && (_jsx("p", { className: "text-xs text-slate-500 mt-0.5", children: description }))] })] }), _jsx("div", { className: "p-5", children: children })] }));
}
// Form Field Component
function FormField({ label, children, className, error, hint, }) {
    return (_jsxs("div", { className: cn('space-y-2', className), children: [_jsx(Label, { className: "text-xs font-medium text-slate-300 uppercase tracking-wider", children: label }), children, hint && !error && (_jsxs("p", { className: "text-xs text-slate-500 flex items-center gap-1", children: [_jsx(AlertCircle, { className: "w-3 h-3" }), hint] })), error && (_jsxs("p", { className: "text-xs text-red-400 flex items-center gap-1", children: [_jsx(AlertCircle, { className: "w-3 h-3" }), error] }))] }));
}
// Number Input Component
function NumberInput({ value, onChange, min, max, placeholder, suffix, }) {
    return (_jsxs("div", { className: "relative", children: [_jsx(Input, { type: "number", value: value, onChange: (e) => onChange(Number(e.target.value)), min: min, max: max, placeholder: placeholder, className: cn('h-10 bg-slate-950/50 border-slate-700/50 text-slate-100', 'focus:border-slate-500/50 focus:ring-slate-500/20', 'placeholder:text-slate-600', suffix && 'pr-12') }), suffix && (_jsx("span", { className: "absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500", children: suffix }))] }));
}
// Toggle Field Component
function ToggleField({ label, description, checked, onChange, icon: Icon, }) {
    return (_jsxs("div", { className: "flex items-start gap-4 p-4 rounded-lg bg-slate-900/50 border border-slate-700/40 transition-colors", children: [_jsx("div", { className: "flex items-center justify-center w-9 h-9 rounded-md bg-slate-800/70 shrink-0", children: _jsx(Icon, { className: "w-5 h-5 text-slate-400" }) }), _jsxs("div", { className: "flex-1 min-w-0", children: [_jsxs("div", { className: "flex items-center justify-between gap-4", children: [_jsx("span", { className: "text-sm font-medium text-slate-200", children: label }), _jsx(Switch, { checked: checked, onCheckedChange: onChange, className: "data-[state=checked]:bg-emerald-500" })] }), description && (_jsx("p", { className: "text-xs text-slate-500 mt-1", children: description }))] })] }));
}
// Theme Selector Component
function ThemeSelector() {
    const { theme, setTheme } = useTheme();
    const options = [
        {
            value: 'light',
            label: '浅色',
            description: '明亮的浅色主题',
            icon: Sun,
        },
        {
            value: 'dark',
            label: '深色',
            description: '护眼的深色主题',
            icon: Moon,
        },
        {
            value: 'system',
            label: '跟随系统',
            description: '自动跟随操作系统设置',
            icon: Monitor,
        },
    ];
    return (_jsx("div", { className: "grid grid-cols-3 gap-3", children: options.map((option) => {
            const Icon = option.icon;
            const isActive = theme === option.value;
            return (_jsxs("button", { onClick: () => setTheme(option.value), className: cn('relative flex flex-col items-center justify-center gap-2 p-4 rounded-lg', 'border transition-colors duration-150', isActive
                    ? 'bg-emerald-500/10 border-emerald-500/40'
                    : 'bg-slate-900/40 border-slate-700/50 hover:border-slate-600'), children: [_jsx("div", { className: cn('flex items-center justify-center w-9 h-9 rounded-md', 'transition-colors duration-150', isActive ? 'bg-emerald-500/[0.15]' : 'bg-slate-800/60'), children: _jsx(Icon, { className: cn('w-4 h-4 transition-colors duration-150', isActive ? 'text-emerald-400' : 'text-slate-400') }) }), _jsx("span", { className: cn('text-sm font-medium transition-colors duration-150', isActive ? 'text-emerald-400' : 'text-slate-300'), children: option.label }), _jsx("span", { className: "text-xs text-slate-500", children: option.description }), isActive && (_jsx("div", { className: "absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-500 flex items-center justify-center", children: _jsx(CheckCircle2, { className: "w-2 h-2 text-white" }) }))] }, option.value));
        }) }));
}
// Main Component
export function GeneralSettingsTab({ settings, onSave }) {
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);
    const [error, setError] = useState(null);
    // Form State
    const [formState, setFormState] = useState({
        promptProfile: 'zhenguan_governance',
        refreshInterval: 3,
        autoRefresh: true,
        pmInterval: 20,
        pmTimeout: 0,
        pmRunsDirector: true,
        pmDirectorShowOutput: true,
        pmDirectorTimeout: 600,
        pmDirectorIterations: 1,
        pmDirectorMatchMode: 'latest',
        pmShowOutput: true,
        pmMaxFailures: 5,
        pmMaxBlocked: 5,
        pmMaxSame: 3,
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
        slmEnabled: false,
        qaEnabled: true,
        ramdiskRoot: '',
        jsonLogPath: 'runtime/events/pm.events.jsonl',
        showMemory: false,
        debugTracing: false,
        ioFsyncMode: 'strict',
        memoryRefsMode: 'soft',
        backendPort: 49977,
        frontendPort: 5173,
    });
    // Initialize from settings
    useEffect(() => {
        if (!settings)
            return;
        setFormState((prev) => ({
            ...prev,
            promptProfile: settings.prompt_profile ?? prev.promptProfile,
            refreshInterval: settings.refresh_interval ?? prev.refreshInterval,
            autoRefresh: settings.auto_refresh ?? prev.autoRefresh,
            pmInterval: settings.interval ?? prev.pmInterval,
            pmTimeout: settings.timeout ?? prev.pmTimeout,
            pmRunsDirector: settings.pm_runs_director ?? prev.pmRunsDirector,
            pmDirectorShowOutput: settings.pm_director_show_output ?? prev.pmDirectorShowOutput,
            pmDirectorTimeout: settings.pm_director_timeout ?? prev.pmDirectorTimeout,
            pmDirectorIterations: settings.pm_director_iterations ?? prev.pmDirectorIterations,
            pmDirectorMatchMode: settings.pm_director_match_mode ?? prev.pmDirectorMatchMode,
            pmShowOutput: settings.pm_show_output ?? prev.pmShowOutput,
            pmMaxFailures: settings.pm_max_failures ?? prev.pmMaxFailures,
            pmMaxBlocked: settings.pm_max_blocked ?? prev.pmMaxBlocked,
            pmMaxSame: settings.pm_max_same ?? prev.pmMaxSame,
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
            slmEnabled: settings.slm_enabled ?? prev.slmEnabled,
            qaEnabled: settings.qa_enabled ?? prev.qaEnabled,
            ramdiskRoot: settings.ramdisk_root ?? prev.ramdiskRoot,
            jsonLogPath: settings.json_log_path ?? prev.jsonLogPath,
            showMemory: settings.show_memory ?? prev.showMemory,
            debugTracing: settings.debug_tracing ?? prev.debugTracing,
            ioFsyncMode: settings.io_fsync_mode ?? prev.ioFsyncMode,
            memoryRefsMode: settings.memory_refs_mode ?? prev.memoryRefsMode,
            backendPort: settings.backend_port ?? prev.backendPort,
            frontendPort: settings.frontend_port ?? prev.frontendPort,
        }));
    }, [settings]);
    const handleSave = async () => {
        setSaving(true);
        setError(null);
        try {
            await onSave({
                prompt_profile: formState.promptProfile,
                refresh_interval: formState.refreshInterval,
                auto_refresh: formState.autoRefresh,
                interval: formState.pmInterval,
                timeout: formState.pmTimeout,
                pm_runs_director: formState.pmRunsDirector,
                pm_director_show_output: formState.pmDirectorShowOutput,
                pm_director_timeout: formState.pmDirectorTimeout,
                pm_director_iterations: formState.pmDirectorIterations,
                pm_director_match_mode: formState.pmDirectorMatchMode,
                pm_show_output: formState.pmShowOutput,
                pm_max_failures: formState.pmMaxFailures,
                pm_max_blocked: formState.pmMaxBlocked,
                pm_max_same: formState.pmMaxSame,
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
                slm_enabled: formState.slmEnabled,
                qa_enabled: formState.qaEnabled,
                ramdisk_root: formState.ramdiskRoot,
                json_log_path: formState.jsonLogPath,
                show_memory: formState.showMemory,
                debug_tracing: formState.debugTracing,
                io_fsync_mode: formState.ioFsyncMode,
                memory_refs_mode: formState.memoryRefsMode,
                backend_port: formState.backendPort,
                frontend_port: formState.frontendPort,
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
    return (_jsxs("div", { className: "space-y-6 pb-20", children: [_jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { children: [_jsxs("h2", { className: "text-xl font-bold text-slate-100 flex items-center gap-2", children: [_jsx(Settings, { className: "w-6 h-6 text-emerald-400" }), "\u901A\u7528\u8BBE\u7F6E"] }), _jsx("p", { className: "text-sm text-slate-400 mt-1", children: "\u914D\u7F6E Polaris \u6838\u5FC3\u53C2\u6570\u3001\u7F51\u7EDC\u7AEF\u53E3\u548C\u7CFB\u7EDF\u884C\u4E3A" })] }), _jsx("button", { onClick: handleSave, disabled: saving, className: cn('flex items-center gap-2 px-5 py-2 rounded-lg font-medium text-sm', 'transition-colors duration-150', saved
                            ? 'bg-emerald-500/[0.15] text-emerald-400 border border-emerald-500/30'
                            : 'bg-emerald-600 text-white hover:bg-emerald-700'), children: saving ? (_jsxs(_Fragment, { children: [_jsx(Loader2, { className: "w-4 h-4 animate-spin" }), "\u4FDD\u5B58\u4E2D..."] })) : saved ? (_jsxs(_Fragment, { children: [_jsx(CheckCircle2, { className: "w-4 h-4" }), "\u5DF2\u4FDD\u5B58"] })) : (_jsxs(_Fragment, { children: [_jsx(Sparkles, { className: "w-4 h-4" }), "\u4FDD\u5B58\u8BBE\u7F6E"] })) })] }), error && (_jsxs("div", { className: "flex items-center gap-2 p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400", children: [_jsx(AlertCircle, { className: "w-5 h-5 shrink-0" }), _jsx("span", { className: "text-sm", children: error })] })), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Globe, { className: "w-4 h-4 text-slate-400" }), "\u7F51\u7EDC\u4E0E\u7AEF\u53E3\u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4", children: [_jsx(SectionCard, { title: "\u540E\u7AEF\u670D\u52A1\u7AEF\u53E3", icon: Server, description: "Backend API \u670D\u52A1\u76D1\u542C\u7AEF\u53E3", children: _jsx("div", { className: "space-y-4", children: _jsx(FormField, { label: "\u7AEF\u53E3\u53F7", hint: "\u9ED8\u8BA4 49977\uFF0C\u4FEE\u6539\u540E\u9700\u91CD\u542F\u670D\u52A1", children: _jsx(NumberInput, { value: formState.backendPort, onChange: (v) => updateField('backendPort', v), min: 1024, max: 65535 }) }) }) }), _jsx(SectionCard, { title: "\u524D\u7AEF\u5F00\u53D1\u7AEF\u53E3", icon: Zap, description: "Vite \u5F00\u53D1\u670D\u52A1\u5668\u7AEF\u53E3", children: _jsx("div", { className: "space-y-4", children: _jsx(FormField, { label: "\u7AEF\u53E3\u53F7", hint: "\u9ED8\u8BA4 5173\uFF0C\u5F00\u53D1\u6A21\u5F0F\u4E0B\u751F\u6548", children: _jsx(NumberInput, { value: formState.frontendPort, onChange: (v) => updateField('frontendPort', v), min: 1024, max: 65535 }) }) }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Clock, { className: "w-4 h-4 text-emerald-400" }), "PM \u8C03\u5EA6\u5668\u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4", children: [_jsx(SectionCard, { title: "\u57FA\u7840\u53C2\u6570", icon: Settings, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u6267\u884C\u95F4\u9694", hint: "PM \u5FAA\u73AF\u95F4\u9694\u65F6\u95F4", children: _jsx(NumberInput, { value: formState.pmInterval, onChange: (v) => updateField('pmInterval', v), min: 1, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8D85\u65F6\u65F6\u95F4", hint: "0 \u8868\u793A\u65E0\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.pmTimeout, onChange: (v) => updateField('pmTimeout', v), min: 0, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u63D0\u793A\u8BCD\u914D\u7F6E", children: _jsxs(Select, { value: formState.promptProfile, onValueChange: (v) => updateField('promptProfile', v), children: [_jsx(SelectTrigger, { className: "bg-slate-950/50 border-slate-700/50", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { className: "bg-slate-900 border-slate-700", children: [_jsx(SelectItem, { value: "zhenguan_governance", children: "\u8D1E\u89C2\u653F\u8981" }), _jsx(SelectItem, { value: "modern_pm", children: "\u73B0\u4EE3\u9879\u76EE\u7BA1\u7406" }), _jsx(SelectItem, { value: "agile_coach", children: "\u654F\u6377\u6559\u7EC3" })] })] }) })] }) }), _jsx(SectionCard, { title: "\u5168\u94FE\u8DEF\u6267\u884C", icon: Layers, children: _jsxs("div", { className: "space-y-3", children: [_jsx(ToggleField, { label: "\u542F\u7528\u6267\u884C\u9636\u6BB5", description: "PM\u3001Chief Engineer\u3001Director \u987A\u5E8F\u6267\u884C", checked: formState.pmRunsDirector, onChange: (v) => updateField('pmRunsDirector', v), icon: Server }), _jsx(ToggleField, { label: "\u663E\u793A\u8F93\u51FA", description: "\u5728\u7EC8\u7AEF\u663E\u793A Director \u8F93\u51FA", checked: formState.pmDirectorShowOutput, onChange: (v) => updateField('pmDirectorShowOutput', v), icon: Terminal }), _jsx(FormField, { label: "\u8D85\u65F6\u65F6\u95F4", className: "mt-4", children: _jsx(NumberInput, { value: formState.pmDirectorTimeout, onChange: (v) => updateField('pmDirectorTimeout', v), min: 60, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8FED\u4EE3\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmDirectorIterations, onChange: (v) => updateField('pmDirectorIterations', v), min: 1, max: 100, suffix: "\u6B21" }) })] }) }), _jsx(SectionCard, { title: "\u6545\u969C\u6062\u590D", icon: RotateCcw, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u6700\u5927\u5931\u8D25\u6B21\u6570", hint: "\u8D85\u8FC7\u540E\u6682\u505C\u4EFB\u52A1", children: _jsx(NumberInput, { value: formState.pmMaxFailures, onChange: (v) => updateField('pmMaxFailures', v), min: 1, max: 20, suffix: "\u6B21" }) }), _jsx(FormField, { label: "\u6700\u5927\u963B\u585E\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmMaxBlocked, onChange: (v) => updateField('pmMaxBlocked', v), min: 1, max: 20, suffix: "\u6B21" }) }), _jsx(FormField, { label: "\u6700\u5927\u91CD\u590D\u6B21\u6570", children: _jsx(NumberInput, { value: formState.pmMaxSame, onChange: (v) => updateField('pmMaxSame', v), min: 1, max: 10, suffix: "\u6B21" }) })] }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Cpu, { className: "w-4 h-4 text-slate-400" }), "Director \u6267\u884C\u5668\u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4", children: [_jsx(SectionCard, { title: "\u6267\u884C\u6A21\u5F0F", icon: Activity, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u6267\u884C\u6A21\u5F0F", children: _jsxs(Select, { value: formState.directorExecutionMode, onValueChange: (v) => updateField('directorExecutionMode', v), children: [_jsx(SelectTrigger, { className: "bg-slate-950/50 border-slate-700/50", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { className: "bg-slate-900 border-slate-700", children: [_jsx(SelectItem, { value: "serial", children: "\u4E32\u884C (Serial)" }), _jsx(SelectItem, { value: "parallel", children: "\u5E76\u884C (Parallel)" })] })] }) }), formState.directorExecutionMode === 'parallel' && (_jsx(FormField, { label: "\u6700\u5927\u5E76\u884C\u4EFB\u52A1", children: _jsx(NumberInput, { value: formState.directorMaxParallelTasks, onChange: (v) => updateField('directorMaxParallelTasks', v), min: 1, max: 10, suffix: "\u4E2A" }) })), _jsx(ToggleField, { label: "\u65E0\u9650\u5FAA\u73AF\u6A21\u5F0F", description: "\u6301\u7EED\u6267\u884C\u4E0D\u505C\u6B62", checked: formState.directorForever, onChange: (v) => updateField('directorForever', v), icon: RotateCcw }), _jsx(ToggleField, { label: "\u663E\u793A\u8F93\u51FA", description: "\u663E\u793A\u6267\u884C\u65E5\u5FD7", checked: formState.directorShowOutput, onChange: (v) => updateField('directorShowOutput', v), icon: Terminal })] }) }), _jsx(SectionCard, { title: "\u8D85\u65F6\u914D\u7F6E", icon: Clock, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u5C31\u7EEA\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorReadyTimeoutSeconds, onChange: (v) => updateField('directorReadyTimeoutSeconds', v), min: 5, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8BA4\u9886\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorClaimTimeoutSeconds, onChange: (v) => updateField('directorClaimTimeoutSeconds', v), min: 5, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u9636\u6BB5\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorPhaseTimeoutSeconds, onChange: (v) => updateField('directorPhaseTimeoutSeconds', v), min: 60, suffix: "\u79D2" }) })] }) }), _jsx(SectionCard, { title: "\u4EFB\u52A1\u8D85\u65F6", icon: AlertCircle, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "\u5B8C\u6210\u8D85\u65F6", children: _jsx(NumberInput, { value: formState.directorCompleteTimeoutSeconds, onChange: (v) => updateField('directorCompleteTimeoutSeconds', v), min: 10, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u4EFB\u52A1\u8D85\u65F6", hint: "\u5355\u4E2A\u4EFB\u52A1\u6700\u5927\u6267\u884C\u65F6\u95F4", children: _jsx(NumberInput, { value: formState.directorTaskTimeoutSeconds, onChange: (v) => updateField('directorTaskTimeoutSeconds', v), min: 300, suffix: "\u79D2" }) }), _jsx(FormField, { label: "\u8FED\u4EE3\u6B21\u6570", children: _jsx(NumberInput, { value: formState.directorIterations, onChange: (v) => updateField('directorIterations', v), min: 1, max: 100, suffix: "\u6B21" }) })] }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(HardDrive, { className: "w-4 h-4 text-amber-400" }), "\u5B58\u50A8\u4E0E IO \u914D\u7F6E"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4", children: [_jsx(SectionCard, { title: "\u5B58\u50A8\u8DEF\u5F84", icon: HardDrive, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "Ramdisk \u6839\u76EE\u5F55", hint: "\u53EF\u9009\uFF0C\u7528\u4E8E\u52A0\u901F\u4E34\u65F6\u6587\u4EF6", children: _jsx(Input, { value: formState.ramdiskRoot, onChange: (e) => updateField('ramdiskRoot', e.target.value), placeholder: "\u4F8B\u5982: X:\\\\ \u6216 /mnt/ramdisk", className: "bg-slate-950/50 border-slate-700/50 text-slate-100 placeholder:text-slate-600" }) }), _jsx(FormField, { label: "JSONL \u65E5\u5FD7\u8DEF\u5F84", hint: "\u4E8B\u4EF6\u65E5\u5FD7\u5B58\u50A8\u4F4D\u7F6E", children: _jsx(Input, { value: formState.jsonLogPath, onChange: (e) => updateField('jsonLogPath', e.target.value), className: "bg-slate-950/50 border-slate-700/50 text-slate-100" }) })] }) }), _jsx(SectionCard, { title: "IO \u6A21\u5F0F", icon: FileText, children: _jsxs("div", { className: "space-y-4", children: [_jsx(FormField, { label: "FSync \u6A21\u5F0F", children: _jsxs(Select, { value: formState.ioFsyncMode, onValueChange: (v) => updateField('ioFsyncMode', v), children: [_jsx(SelectTrigger, { className: "bg-slate-950/50 border-slate-700/50", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { className: "bg-slate-900 border-slate-700", children: [_jsx(SelectItem, { value: "strict", children: "\u4E25\u683C (Strict) - \u6700\u9AD8\u6301\u4E45\u5316" }), _jsx(SelectItem, { value: "relaxed", children: "\u5BBD\u677E (Relaxed) - \u66F4\u9AD8\u6027\u80FD" })] })] }) }), _jsx(FormField, { label: "\u5185\u5B58\u5F15\u7528\u6A21\u5F0F", children: _jsxs(Select, { value: formState.memoryRefsMode, onValueChange: (v) => updateField('memoryRefsMode', v), children: [_jsx(SelectTrigger, { className: "bg-slate-950/50 border-slate-700/50", children: _jsx(SelectValue, {}) }), _jsxs(SelectContent, { className: "bg-slate-900 border-slate-700", children: [_jsx(SelectItem, { value: "strict", children: "\u4E25\u683C - \u5F3A\u5F15\u7528\u4FDD\u8BC1" }), _jsx(SelectItem, { value: "soft", children: "\u8F6F\u5F15\u7528 - \u5E73\u8861\u6A21\u5F0F" }), _jsx(SelectItem, { value: "off", children: "\u5173\u95ED - \u65E0\u5F15\u7528\u8FFD\u8E2A" })] })] }) })] }) })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Palette, { className: "w-4 h-4 text-slate-400" }), "\u5916\u89C2\u8BBE\u7F6E"] }), _jsx(SectionCard, { title: "\u4E3B\u9898\u914D\u7F6E", icon: Sparkles, description: "\u9009\u62E9\u5E94\u7528\u5916\u89C2\u4E3B\u9898", children: _jsx(ThemeSelector, {}) })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(Sparkles, { className: "w-4 h-4 text-slate-400" }), "\u529F\u80FD\u5F00\u5173"] }), _jsxs("div", { className: "grid grid-cols-1 md:grid-cols-2 gap-4", children: [_jsxs("div", { className: "space-y-3", children: [_jsx(ToggleField, { label: "\u81EA\u52A8\u5237\u65B0", description: "\u81EA\u52A8\u5237\u65B0\u4EFB\u52A1\u72B6\u6001", checked: formState.autoRefresh, onChange: (v) => updateField('autoRefresh', v), icon: RotateCcw }), _jsx(ToggleField, { label: "\u663E\u793A\u5185\u5B58\u4F7F\u7528", description: "\u5728 UI \u4E2D\u663E\u793A\u5185\u5B58\u7EDF\u8BA1", checked: formState.showMemory, onChange: (v) => updateField('showMemory', v), icon: MemoryStick }), _jsx(ToggleField, { label: "SLM \u6A21\u5F0F", description: "\u542F\u7528\u5C0F\u578B\u8BED\u8A00\u6A21\u578B\u4F18\u5316", checked: formState.slmEnabled, onChange: (v) => updateField('slmEnabled', v), icon: Cpu })] }), _jsxs("div", { className: "space-y-3", children: [_jsx(ToggleField, { label: "QA \u5BA1\u67E5", description: "\u542F\u7528\u8D28\u91CF\u5BA1\u67E5\u6D41\u7A0B", checked: formState.qaEnabled, onChange: (v) => updateField('qaEnabled', v), icon: CheckCircle2 }), _jsx(ToggleField, { label: "\u8C03\u8BD5\u8FFD\u8E2A", description: "\u542F\u7528\u8BE6\u7EC6\u8C03\u8BD5\u65E5\u5FD7", checked: formState.debugTracing, onChange: (v) => updateField('debugTracing', v), icon: Bug }), _jsx(ToggleField, { label: "\u663E\u793A PM \u8F93\u51FA", description: "\u5728\u7EC8\u7AEF\u663E\u793A PM \u65E5\u5FD7", checked: formState.pmShowOutput, onChange: (v) => updateField('pmShowOutput', v), icon: Terminal })] })] })] }), _jsxs("section", { className: "space-y-4", children: [_jsxs("div", { className: "flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider", children: [_jsx(RotateCcw, { className: "w-4 h-4 text-slate-400" }), "\u5237\u65B0\u8BBE\u7F6E"] }), _jsx(SectionCard, { title: "\u81EA\u52A8\u5237\u65B0\u95F4\u9694", icon: Clock, children: _jsxs("div", { className: "flex items-center gap-4", children: [_jsx("div", { className: "flex-1", children: _jsx(NumberInput, { value: formState.refreshInterval, onChange: (v) => updateField('refreshInterval', v), min: 1, max: 60, suffix: "\u79D2" }) }), _jsxs("div", { className: "text-sm text-slate-400", children: ["\u5F53\u524D: ", formState.refreshInterval, " \u79D2"] })] }) })] })] }));
}
