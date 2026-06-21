/**
 * Workflow Settings Tab
 *
 * Configuration for workflow execution, task management, and
 * runtime behavior settings.
 */

import { useState, useEffect } from 'react';
import {
  Workflow,
  Clock,
  Cpu,
  Activity,
  RotateCcw,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Layers,
  Terminal,
  Sparkles,
} from 'lucide-react';
import { Input } from '@/app/components/ui/input';
import { Label } from '@/app/components/ui/label';
import { Switch } from '@/app/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/app/components/ui/select';
import { cn } from '@/app/components/ui/utils';

interface WorkflowSettingsTabProps {
  settings: {
    director_iterations?: number;
    director_execution_mode?: 'serial' | 'parallel' | string;
    director_max_parallel_tasks?: number;
    director_ready_timeout_seconds?: number;
    director_claim_timeout_seconds?: number;
    director_phase_timeout_seconds?: number;
    director_complete_timeout_seconds?: number;
    director_task_timeout_seconds?: number;
    director_forever?: boolean;
    director_show_output?: boolean;
    pm_runs_director?: boolean;
    pm_director_show_output?: boolean;
    pm_director_timeout?: number;
    pm_director_iterations?: number;
    pm_director_match_mode?: string;
    pm_max_failures?: number;
    pm_max_blocked?: number;
    pm_max_same?: number;
    qa_enabled?: boolean;
    slm_enabled?: boolean;
  } | null;
  onSave: (payload: Record<string, unknown>) => Promise<void>;
}

// Section Card Component
function SectionCard({
  children,
  className,
  title,
  icon: Icon,
  description,
}: {
  children: React.ReactNode;
  className?: string;
  title: string;
  icon: React.ElementType;
  description?: string;
}) {
  return (
    <div
      className={cn(
        'rounded-lg border border-slate-700/60 bg-slate-800/70 shadow-sm',
        className
      )}
    >
      <div className="flex items-center gap-3 px-5 py-3 border-b border-slate-700/50">
        <div className="flex items-center justify-center w-8 h-8 rounded-md bg-slate-700/60">
          <Icon className="w-4 h-4 text-slate-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-slate-200">{title}</h3>
          {description && <p className="text-xs text-slate-500 mt-0.5">{description}</p>}
        </div>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

// Form Field Component
function FormField({
  label,
  children,
  className,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
  hint?: string;
}) {
  return (
    <div className={cn('space-y-2', className)}>
      <Label className="text-xs font-medium text-slate-300 uppercase tracking-wider">{label}</Label>
      {children}
      {hint && (
        <p className="text-xs text-slate-500 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" />
          {hint}
        </p>
      )}
    </div>
  );
}

// Number Input Component
function NumberInput({
  value,
  onChange,
  min,
  max,
  suffix,
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  return (
    <div className="relative">
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        min={min}
        max={max}
        className={cn(
          'h-10 bg-slate-950/50 border-slate-700/50 text-slate-100',
          'focus:border-slate-500/50 focus:ring-slate-500/20',
          'placeholder:text-slate-600',
          suffix && 'pr-12'
        )}
      />
      {suffix && (
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">{suffix}</span>
      )}
    </div>
  );
}

// Toggle Field Component
function ToggleField({
  label,
  description,
  checked,
  onChange,
  icon: Icon,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  icon: React.ElementType;
}) {
  return (
    <div className="flex items-start gap-4 p-4 rounded-lg bg-slate-900/50 border border-slate-700/40 transition-colors">
      <div className="flex items-center justify-center w-9 h-9 rounded-md bg-slate-800/70 shrink-0">
        <Icon className="w-5 h-5 text-slate-400" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-4">
          <span className="text-sm font-medium text-slate-200">{label}</span>
          <Switch
            checked={checked}
            onCheckedChange={onChange}
            className="data-[state=checked]:bg-emerald-500"
          />
        </div>
        {description && <p className="text-xs text-slate-500 mt-1">{description}</p>}
      </div>
    </div>
  );
}

export function WorkflowSettingsTab({ settings, onSave }: WorkflowSettingsTabProps) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [formState, setFormState] = useState({
    directorIterations: 1,
    directorExecutionMode: 'parallel' as 'serial' | 'parallel',
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
  });

  useEffect(() => {
    if (!settings) return;
    setFormState((prev) => ({
      ...prev,
      directorIterations: settings.director_iterations ?? prev.directorIterations,
      directorExecutionMode: (settings.director_execution_mode as 'serial' | 'parallel') ?? prev.directorExecutionMode,
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
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const updateField = <K extends keyof typeof formState>(field: K, value: (typeof formState)[K]) => {
    setFormState((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="space-y-6 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
            <Workflow className="w-6 h-6 text-slate-400" />
            工作流设置
          </h2>
          <p className="text-sm text-slate-400 mt-1">配置 Director 执行器、PM 调度器与工作流行为</p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className={cn(
            'flex items-center gap-2 px-5 py-2 rounded-lg font-medium text-sm',
            'transition-colors duration-150',
            saved
              ? 'bg-emerald-500/[0.15] text-emerald-400 border border-emerald-500/30'
              : 'bg-emerald-600 text-white hover:bg-emerald-700'
          )}
        >
          {saving ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              保存中...
            </>
          ) : saved ? (
            <>
              <CheckCircle2 className="w-4 h-4" />
              已保存
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4" />
              保存设置
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 p-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {/* Director Settings Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Cpu className="w-4 h-4 text-slate-400" />
          Director 执行器配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <SectionCard title="执行模式" icon={Activity}>
            <div className="space-y-4">
              <FormField label="执行模式">
                <Select
                  value={formState.directorExecutionMode}
                  onValueChange={(v: 'serial' | 'parallel') => updateField('directorExecutionMode', v)}
                >
                  <SelectTrigger className="bg-slate-950/50 border-slate-700/50">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-900 border-slate-700">
                    <SelectItem value="serial">串行 (Serial)</SelectItem>
                    <SelectItem value="parallel">并行 (Parallel)</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>

              {formState.directorExecutionMode === 'parallel' && (
                <FormField label="最大并行任务">
                  <NumberInput
                    value={formState.directorMaxParallelTasks}
                    onChange={(v) => updateField('directorMaxParallelTasks', v)}
                    min={1}
                    max={10}
                    suffix="个"
                  />
                </FormField>
              )}

              <ToggleField
                label="无限循环模式"
                description="持续执行不停止"
                checked={formState.directorForever}
                onChange={(v) => updateField('directorForever', v)}
                icon={RotateCcw}
              />

              <ToggleField
                label="显示输出"
                description="显示执行日志"
                checked={formState.directorShowOutput}
                onChange={(v) => updateField('directorShowOutput', v)}
                icon={Terminal}
              />
            </div>
          </SectionCard>

          <SectionCard title="超时配置" icon={Clock}>
            <div className="space-y-4">
              <FormField label="就绪超时">
                <NumberInput
                  value={formState.directorReadyTimeoutSeconds}
                  onChange={(v) => updateField('directorReadyTimeoutSeconds', v)}
                  min={5}
                  suffix="秒"
                />
              </FormField>

              <FormField label="认领超时">
                <NumberInput
                  value={formState.directorClaimTimeoutSeconds}
                  onChange={(v) => updateField('directorClaimTimeoutSeconds', v)}
                  min={5}
                  suffix="秒"
                />
              </FormField>

              <FormField label="阶段超时">
                <NumberInput
                  value={formState.directorPhaseTimeoutSeconds}
                  onChange={(v) => updateField('directorPhaseTimeoutSeconds', v)}
                  min={60}
                  suffix="秒"
                />
              </FormField>
            </div>
          </SectionCard>

          <SectionCard title="任务超时" icon={AlertCircle}>
            <div className="space-y-4">
              <FormField label="完成超时">
                <NumberInput
                  value={formState.directorCompleteTimeoutSeconds}
                  onChange={(v) => updateField('directorCompleteTimeoutSeconds', v)}
                  min={10}
                  suffix="秒"
                />
              </FormField>

              <FormField label="任务超时" hint="单个任务最大执行时间">
                <NumberInput
                  value={formState.directorTaskTimeoutSeconds}
                  onChange={(v) => updateField('directorTaskTimeoutSeconds', v)}
                  min={300}
                  suffix="秒"
                />
              </FormField>

              <FormField label="迭代次数">
                <NumberInput
                  value={formState.directorIterations}
                  onChange={(v) => updateField('directorIterations', v)}
                  min={1}
                  max={100}
                  suffix="次"
                />
              </FormField>
            </div>
          </SectionCard>
        </div>
      </section>

      {/* PM Integration Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Layers className="w-4 h-4 text-emerald-400" />
          PM 集成配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <SectionCard title="全链路执行" icon={Layers}>
            <div className="space-y-3">
              <ToggleField
                label="启用执行阶段"
                description="PM、Chief Engineer、Director 顺序执行"
                checked={formState.pmRunsDirector}
                onChange={(v) => updateField('pmRunsDirector', v)}
                icon={Cpu}
              />

              <ToggleField
                label="显示输出"
                description="在终端显示 Director 输出"
                checked={formState.pmDirectorShowOutput}
                onChange={(v) => updateField('pmDirectorShowOutput', v)}
                icon={Terminal}
              />

              <FormField label="超时时间" className="mt-4">
                <NumberInput
                  value={formState.pmDirectorTimeout}
                  onChange={(v) => updateField('pmDirectorTimeout', v)}
                  min={60}
                  suffix="秒"
                />
              </FormField>

              <FormField label="迭代次数">
                <NumberInput
                  value={formState.pmDirectorIterations}
                  onChange={(v) => updateField('pmDirectorIterations', v)}
                  min={1}
                  max={100}
                  suffix="次"
                />
              </FormField>
            </div>
          </SectionCard>

          <SectionCard title="故障恢复" icon={RotateCcw}>
            <div className="space-y-4">
              <FormField label="最大失败次数" hint="超过后暂停任务">
                <NumberInput
                  value={formState.pmMaxFailures}
                  onChange={(v) => updateField('pmMaxFailures', v)}
                  min={1}
                  max={20}
                  suffix="次"
                />
              </FormField>

              <FormField label="最大阻塞次数">
                <NumberInput
                  value={formState.pmMaxBlocked}
                  onChange={(v) => updateField('pmMaxBlocked', v)}
                  min={1}
                  max={20}
                  suffix="次"
                />
              </FormField>

              <FormField label="最大重复次数">
                <NumberInput
                  value={formState.pmMaxSame}
                  onChange={(v) => updateField('pmMaxSame', v)}
                  min={1}
                  max={10}
                  suffix="次"
                />
              </FormField>
            </div>
          </SectionCard>
        </div>
      </section>

      {/* Features Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Sparkles className="w-4 h-4 text-slate-400" />
          功能开关
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <ToggleField
            label="QA 审查"
            description="启用质量审查流程"
            checked={formState.qaEnabled}
            onChange={(v) => updateField('qaEnabled', v)}
            icon={CheckCircle2}
          />

          <ToggleField
            label="SLM 模式"
            description="启用小型语言模型优化"
            checked={formState.slmEnabled}
            onChange={(v) => updateField('slmEnabled', v)}
            icon={Cpu}
          />
        </div>
      </section>
    </div>
  );
}
