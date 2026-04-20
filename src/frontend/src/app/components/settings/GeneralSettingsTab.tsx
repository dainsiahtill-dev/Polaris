/**
 * GeneralSettingsTab - 通用设置标签页
 * 采用 Glassmorphism 设计风格，支持端口/API Key/LLM系统配置
 */
import { useState, useEffect } from 'react';
import {
  Settings,
  Clock,
  Zap,
  HardDrive,
  FileText,
  Server,
  RotateCcw,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Cpu,
  MemoryStick,
  Terminal,
  Activity,
  Globe,
  Key,
  Layers,
  Sparkles,
  Bug,
  Shield,
  Database,
  Palette,
  Sun,
  Moon,
  Monitor,
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
import { useTheme, type Theme } from '@/app/hooks/useTheme';

// Types
interface GeneralSettingsTabProps {
  settings: {
    prompt_profile?: string;
    interval?: number;
    timeout?: number;
    refresh_interval?: number;
    auto_refresh?: boolean;
    show_memory?: boolean;
    io_fsync_mode?: string;
    memory_refs_mode?: string;
    ramdisk_root?: string;
    json_log_path?: string;
    pm_show_output?: boolean;
    pm_runs_director?: boolean;
    pm_director_show_output?: boolean;
    pm_director_timeout?: number;
    pm_director_iterations?: number;
    pm_director_match_mode?: string;
    pm_max_failures?: number;
    pm_max_blocked?: number;
    pm_max_same?: number;
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
    slm_enabled?: boolean;
    qa_enabled?: boolean;
    debug_tracing?: boolean;
    backend_port?: number;
    frontend_port?: number;
  } | null;
  onSave: (payload: Record<string, unknown>) => Promise<void>;
}

// Glass Card Component
function GlassCard({
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
        'group relative overflow-hidden rounded-2xl border border-white/10',
        'bg-gradient-to-br from-slate-800/80 via-slate-900/90 to-slate-950/95',
        'backdrop-blur-xl shadow-2xl shadow-black/20',
        'transition-all duration-300 ease-out',
        'hover:border-emerald-500/30 hover:shadow-emerald-500/10',
        className
      )}
    >
      {/* Subtle gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-br from-emerald-500/5 via-transparent to-cyan-500/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />

      {/* Header */}
      <div className="relative flex items-center gap-3 px-6 py-4 border-b border-white/5">
        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 border border-emerald-500/20">
          <Icon className="w-5 h-5 text-emerald-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-slate-100 tracking-wide">
            {title}
          </h3>
          {description && (
            <p className="text-xs text-slate-400 mt-0.5 truncate">{description}</p>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="relative p-6">{children}</div>
    </div>
  );
}

// Form Field Component
function FormField({
  label,
  children,
  className,
  error,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
  error?: string;
  hint?: string;
}) {
  return (
    <div className={cn('space-y-2', className)}>
      <Label className="text-xs font-medium text-slate-300 uppercase tracking-wider">
        {label}
      </Label>
      {children}
      {hint && !error && (
        <p className="text-xs text-slate-500 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" />
          {hint}
        </p>
      )}
      {error && (
        <p className="text-xs text-red-400 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" />
          {error}
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
  placeholder,
  suffix,
}: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  placeholder?: string;
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
        placeholder={placeholder}
        className={cn(
          'h-10 bg-slate-950/50 border-slate-700/50 text-slate-100',
          'focus:border-emerald-500/50 focus:ring-emerald-500/20',
          'placeholder:text-slate-600',
          suffix && 'pr-12'
        )}
      />
      {suffix && (
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">
          {suffix}
        </span>
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
    <div className="flex items-start gap-4 p-4 rounded-xl bg-slate-950/30 border border-white/5 hover:border-white/10 transition-colors">
      <div className="flex items-center justify-center w-10 h-10 rounded-lg bg-slate-800/50 shrink-0">
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
        {description && (
          <p className="text-xs text-slate-500 mt-1">{description}</p>
        )}
      </div>
    </div>
  );
}

// Theme Selector Component
function ThemeSelector() {
  const { theme, setTheme } = useTheme();

  const options: Array<{
    value: Theme;
    label: string;
    description: string;
    icon: React.ElementType;
  }> = [
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

  return (
    <div className="grid grid-cols-3 gap-3">
      {options.map((option) => {
        const Icon = option.icon;
        const isActive = theme === option.value;

        return (
          <button
            key={option.value}
            onClick={() => setTheme(option.value)}
            className={cn(
              'relative flex flex-col items-center justify-center gap-2 p-4 rounded-xl',
              'border transition-all duration-200',
              'hover:scale-[1.02] hover:shadow-lg',
              isActive
                ? 'bg-gradient-to-br from-emerald-500/20 to-cyan-500/20 border-emerald-500/50 shadow-emerald-500/20'
                : 'bg-slate-950/30 border-white/5 hover:border-white/20'
            )}
          >
            <div
              className={cn(
                'flex items-center justify-center w-10 h-10 rounded-lg',
                'transition-colors duration-200',
                isActive ? 'bg-emerald-500/20' : 'bg-slate-800/50'
              )}
            >
              <Icon
                className={cn(
                  'w-5 h-5 transition-colors duration-200',
                  isActive ? 'text-emerald-400' : 'text-slate-400'
                )}
              />
            </div>
            <span
              className={cn(
                'text-sm font-medium transition-colors duration-200',
                isActive ? 'text-emerald-400' : 'text-slate-300'
              )}
            >
              {option.label}
            </span>
            <span className="text-xs text-slate-500">{option.description}</span>
            {isActive && (
              <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-500 flex items-center justify-center">
                <CheckCircle2 className="w-2 h-2 text-white" />
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

// Main Component
export function GeneralSettingsTab({ settings, onSave }: GeneralSettingsTabProps) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
    directorExecutionMode: 'parallel' as 'serial' | 'parallel',
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
    ioFsyncMode: 'strict' as 'strict' | 'relaxed',
    memoryRefsMode: 'soft' as 'strict' | 'soft' | 'off',
    backendPort: 49977,
    frontendPort: 5173,
  });

  // Initialize from settings
  useEffect(() => {
    if (!settings) return;
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
      directorExecutionMode: (settings.director_execution_mode as 'serial' | 'parallel') ?? prev.directorExecutionMode,
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
      ioFsyncMode: (settings.io_fsync_mode as 'strict' | 'relaxed') ?? prev.ioFsyncMode,
      memoryRefsMode: (settings.memory_refs_mode as 'strict' | 'soft' | 'off') ?? prev.memoryRefsMode,
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
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const updateField = <K extends keyof typeof formState>(
    field: K,
    value: (typeof formState)[K]
  ) => {
    setFormState((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="space-y-6 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
            <Settings className="w-6 h-6 text-emerald-400" />
            通用设置
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            配置 Polaris 核心参数、网络端口和系统行为
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className={cn(
            'flex items-center gap-2 px-6 py-2.5 rounded-xl font-medium text-sm',
            'transition-all duration-200',
            saved
              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
              : 'bg-gradient-to-r from-emerald-500 to-cyan-500 text-white shadow-lg shadow-emerald-500/25 hover:shadow-emerald-500/40 hover:scale-[1.02]'
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
        <div className="flex items-center gap-2 p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400">
          <AlertCircle className="w-5 h-5 shrink-0" />
          <span className="text-sm">{error}</span>
        </div>
      )}

      {/* Network & Ports Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Globe className="w-4 h-4 text-cyan-400" />
          网络与端口配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GlassCard
            title="后端服务端口"
            icon={Server}
            description="Backend API 服务监听端口"
          >
            <div className="space-y-4">
              <FormField label="端口号" hint="默认 49977，修改后需重启服务">
                <NumberInput
                  value={formState.backendPort}
                  onChange={(v) => updateField('backendPort', v)}
                  min={1024}
                  max={65535}
                />
              </FormField>
            </div>
          </GlassCard>

          <GlassCard
            title="前端开发端口"
            icon={Zap}
            description="Vite 开发服务器端口"
          >
            <div className="space-y-4">
              <FormField label="端口号" hint="默认 5173，开发模式下生效">
                <NumberInput
                  value={formState.frontendPort}
                  onChange={(v) => updateField('frontendPort', v)}
                  min={1024}
                  max={65535}
                />
              </FormField>
            </div>
          </GlassCard>
        </div>
      </section>

      {/* PM Settings Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Clock className="w-4 h-4 text-emerald-400" />
          PM 调度器配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <GlassCard title="基础参数" icon={Settings}>
            <div className="space-y-4">
              <FormField label="执行间隔" hint="PM 循环间隔时间">
                <NumberInput
                  value={formState.pmInterval}
                  onChange={(v) => updateField('pmInterval', v)}
                  min={1}
                  suffix="秒"
                />
              </FormField>

              <FormField label="超时时间" hint="0 表示无超时">
                <NumberInput
                  value={formState.pmTimeout}
                  onChange={(v) => updateField('pmTimeout', v)}
                  min={0}
                  suffix="秒"
                />
              </FormField>

              <FormField label="提示词配置">
                <Select
                  value={formState.promptProfile}
                  onValueChange={(v) => updateField('promptProfile', v)}
                >
                  <SelectTrigger className="bg-slate-950/50 border-slate-700/50">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-900 border-slate-700">
                    <SelectItem value="zhenguan_governance">贞观政要</SelectItem>
                    <SelectItem value="modern_pm">现代项目管理</SelectItem>
                    <SelectItem value="agile_coach">敏捷教练</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
            </div>
          </GlassCard>

          <GlassCard title="Director 集成" icon={Layers}>
            <div className="space-y-3">
              <ToggleField
                label="启用 Director"
                description="PM 自动运行 Director"
                checked={formState.pmRunsDirector}
                onChange={(v) => updateField('pmRunsDirector', v)}
                icon={Server}
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
          </GlassCard>

          <GlassCard title="故障恢复" icon={RotateCcw}>
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
          </GlassCard>
        </div>
      </section>

      {/* Director Settings Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Cpu className="w-4 h-4 text-purple-400" />
          Director 执行器配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <GlassCard title="执行模式" icon={Activity}>
            <div className="space-y-4">
              <FormField label="执行模式">
                <Select
                  value={formState.directorExecutionMode}
                  onValueChange={(v: 'serial' | 'parallel') =>
                    updateField('directorExecutionMode', v)
                  }
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
          </GlassCard>

          <GlassCard title="超时配置" icon={Clock}>
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
          </GlassCard>

          <GlassCard title="任务超时" icon={AlertCircle}>
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
          </GlassCard>
        </div>
      </section>

      {/* Storage & IO Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <HardDrive className="w-4 h-4 text-amber-400" />
          存储与 IO 配置
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GlassCard title="存储路径" icon={HardDrive}>
            <div className="space-y-4">
              <FormField label="Ramdisk 根目录" hint="可选，用于加速临时文件">
                <Input
                  value={formState.ramdiskRoot}
                  onChange={(e) => updateField('ramdiskRoot', e.target.value)}
                  placeholder="例如: X:\\ 或 /mnt/ramdisk"
                  className="bg-slate-950/50 border-slate-700/50 text-slate-100 placeholder:text-slate-600"
                />
              </FormField>

              <FormField label="JSONL 日志路径" hint="事件日志存储位置">
                <Input
                  value={formState.jsonLogPath}
                  onChange={(e) => updateField('jsonLogPath', e.target.value)}
                  className="bg-slate-950/50 border-slate-700/50 text-slate-100"
                />
              </FormField>
            </div>
          </GlassCard>

          <GlassCard title="IO 模式" icon={FileText}>
            <div className="space-y-4">
              <FormField label="FSync 模式">
                <Select
                  value={formState.ioFsyncMode}
                  onValueChange={(v: 'strict' | 'relaxed') => updateField('ioFsyncMode', v)}
                >
                  <SelectTrigger className="bg-slate-950/50 border-slate-700/50">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-900 border-slate-700">
                    <SelectItem value="strict">严格 (Strict) - 最高持久化</SelectItem>
                    <SelectItem value="relaxed">宽松 (Relaxed) - 更高性能</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>

              <FormField label="内存引用模式">
                <Select
                  value={formState.memoryRefsMode}
                  onValueChange={(v: 'strict' | 'soft' | 'off') =>
                    updateField('memoryRefsMode', v)
                  }
                >
                  <SelectTrigger className="bg-slate-950/50 border-slate-700/50">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-900 border-slate-700">
                    <SelectItem value="strict">严格 - 强引用保证</SelectItem>
                    <SelectItem value="soft">软引用 - 平衡模式</SelectItem>
                    <SelectItem value="off">关闭 - 无引用追踪</SelectItem>
                  </SelectContent>
                </Select>
              </FormField>
            </div>
          </GlassCard>
        </div>
      </section>

      {/* Theme Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Palette className="w-4 h-4 text-indigo-400" />
          外观设置
        </div>

        <GlassCard title="主题配置" icon={Sparkles} description="选择应用外观主题">
          <ThemeSelector />
        </GlassCard>
      </section>

      {/* Features Section */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <Sparkles className="w-4 h-4 text-pink-400" />
          功能开关
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-3">
            <ToggleField
              label="自动刷新"
              description="自动刷新任务状态"
              checked={formState.autoRefresh}
              onChange={(v) => updateField('autoRefresh', v)}
              icon={RotateCcw}
            />

            <ToggleField
              label="显示内存使用"
              description="在 UI 中显示内存统计"
              checked={formState.showMemory}
              onChange={(v) => updateField('showMemory', v)}
              icon={MemoryStick}
            />

            <ToggleField
              label="SLM 模式"
              description="启用小型语言模型优化"
              checked={formState.slmEnabled}
              onChange={(v) => updateField('slmEnabled', v)}
              icon={Cpu}
            />
          </div>

          <div className="space-y-3">
            <ToggleField
              label="QA 审查"
              description="启用质量审查流程"
              checked={formState.qaEnabled}
              onChange={(v) => updateField('qaEnabled', v)}
              icon={CheckCircle2}
            />

            <ToggleField
              label="调试追踪"
              description="启用详细调试日志"
              checked={formState.debugTracing}
              onChange={(v) => updateField('debugTracing', v)}
              icon={Bug}
            />

            <ToggleField
              label="显示 PM 输出"
              description="在终端显示 PM 日志"
              checked={formState.pmShowOutput}
              onChange={(v) => updateField('pmShowOutput', v)}
              icon={Terminal}
            />
          </div>
        </div>
      </section>

      {/* Refresh Settings */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 uppercase tracking-wider">
          <RotateCcw className="w-4 h-4 text-blue-400" />
          刷新设置
        </div>

        <GlassCard title="自动刷新间隔" icon={Clock}>
          <div className="flex items-center gap-4">
            <div className="flex-1">
              <NumberInput
                value={formState.refreshInterval}
                onChange={(v) => updateField('refreshInterval', v)}
                min={1}
                max={60}
                suffix="秒"
              />
            </div>
            <div className="text-sm text-slate-400">
              当前: {formState.refreshInterval} 秒
            </div>
          </div>
        </GlassCard>
      </section>
    </div>
  );
}
