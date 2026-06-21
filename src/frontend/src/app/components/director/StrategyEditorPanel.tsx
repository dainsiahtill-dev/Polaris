/** StrategyEditorPanel - Director 执行策略编辑器 */
import { useState, useCallback, useEffect, useMemo } from 'react';
import Editor, { loader } from '@monaco-editor/react';
import {
  FileJson,
  CheckCircle2,
  AlertCircle,
  Save,
  RefreshCw,
  Copy,
  Code2,
  SlidersHorizontal,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';

type MonacoWorkerEnvironment = {
  getWorker: (_workerId: unknown, label: string) => Worker;
};

const monacoGlobal = globalThis as unknown as {
  MonacoEnvironment?: MonacoWorkerEnvironment;
};

let localMonacoConfiguration: Promise<void> | null = null;

function configureLocalMonaco(): Promise<void> {
  if (import.meta.env.MODE === 'test') {
    return Promise.resolve();
  }
  if (!localMonacoConfiguration) {
    localMonacoConfiguration = Promise.all([
      import('monaco-editor/esm/vs/editor/editor.api'),
      import('monaco-editor/esm/vs/language/json/monaco.contribution'),
      import('monaco-editor/esm/vs/editor/editor.worker?worker'),
      import('monaco-editor/esm/vs/language/json/json.worker?worker'),
    ]).then(([monacoModule, , editorWorkerModule, jsonWorkerModule]) => {
      const EditorWorker = editorWorkerModule.default;
      const JsonWorker = jsonWorkerModule.default;
      monacoGlobal.MonacoEnvironment = {
        getWorker(_workerId: unknown, label: string) {
          if (label === 'json') {
            return new JsonWorker();
          }
          return new EditorWorker();
        },
      };
      loader.config({ monaco: monacoModule });
    });
  }
  return localMonacoConfiguration;
}

export interface DirectorExecutionStrategy {
  name: string;
  version: string;
  mode: 'serial' | 'parallel';
  limits: {
    iterations: number;
    maxParallelTasks: number;
    readyTimeoutSeconds: number;
    claimTimeoutSeconds: number;
    phaseTimeoutSeconds: number;
    completeTimeoutSeconds: number;
    taskTimeoutSeconds: number;
  };
  observability: {
    forever: boolean;
    showOutput: boolean;
  };
  metadata?: {
    source?: string;
    workspace?: string;
    updatedAt?: string;
  };
}

const DEFAULT_STRATEGY: DirectorExecutionStrategy = {
  name: 'director-default',
  version: '1.0.0',
  mode: 'parallel',
  limits: {
    iterations: 1,
    maxParallelTasks: 3,
    readyTimeoutSeconds: 30,
    claimTimeoutSeconds: 30,
    phaseTimeoutSeconds: 900,
    completeTimeoutSeconds: 30,
    taskTimeoutSeconds: 3600,
  },
  observability: {
    forever: false,
    showOutput: true,
  },
  metadata: {
    source: 'polaris-settings',
  },
};

interface ValidationError {
  path: string;
  message: string;
  line?: number;
}

interface StrategyEditorPanelProps {
  initialStrategy?: string;
  onSave?: (strategy: DirectorExecutionStrategy) => void | Promise<void>;
  onValidate?: (isValid: boolean, errors: ValidationError[]) => void;
  readOnly?: boolean;
  saveState?: 'idle' | 'saving' | 'saved' | 'error';
  saveMessage?: string | null;
  saveButtonLabel?: string;
}

export function StrategyEditorPanel({
  initialStrategy,
  onSave,
  onValidate,
  readOnly = false,
  saveState = 'idle',
  saveMessage,
  saveButtonLabel = '保存',
}: StrategyEditorPanelProps) {
  const [content, setContent] = useState<string>(
    initialStrategy || JSON.stringify(DEFAULT_STRATEGY, null, 2)
  );
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [isValid, setIsValid] = useState(true);
  const [isDirty, setIsDirty] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState('default');
  const [isMonacoReady, setIsMonacoReady] = useState(import.meta.env.MODE === 'test');
  const [monacoLoadFailed, setMonacoLoadFailed] = useState(false);

  const templates = useMemo(() => [
    { id: 'default', name: '标准并行', content: DEFAULT_STRATEGY },
    {
      id: 'serial',
      name: '串行稳态',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'director-serial-safe',
        mode: 'serial',
        limits: { ...DEFAULT_STRATEGY.limits, maxParallelTasks: 1, phaseTimeoutSeconds: 1200 },
      },
    },
    {
      id: 'fast',
      name: '快速并发',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'director-fast-parallel',
        mode: 'parallel',
        limits: { ...DEFAULT_STRATEGY.limits, iterations: 2, maxParallelTasks: 5, phaseTimeoutSeconds: 420, taskTimeoutSeconds: 1800 },
      },
    },
    {
      id: 'quiet',
      name: '安静后台',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'director-background',
        observability: { forever: false, showOutput: false },
      },
    },
  ], []);

  const validateJson = useCallback((jsonString: string): ValidationError[] => {
    const validationErrors: ValidationError[] = [];

    try {
      const parsed = JSON.parse(jsonString);
      validationErrors.push(...validateDirectorStrategy(parsed));
    } catch (e) {
      const error = e as Error;
      const lineMatch = error.message.match(/position (\d+)/);
      const position = lineMatch ? parseInt(lineMatch[1]) : 0;
      const lines = jsonString.substring(0, position).split('\n');

      validationErrors.push({
        path: 'JSON',
        message: error.message,
        line: lines.length,
      });
    }

    return validationErrors;
  }, []);

  useEffect(() => {
    let mounted = true;
    configureLocalMonaco()
      .then(() => {
        if (mounted) {
          setIsMonacoReady(true);
        }
      })
      .catch(() => {
        if (mounted) {
          setMonacoLoadFailed(true);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  function validateDirectorStrategy(obj: unknown): ValidationError[] {
    const errors: ValidationError[] = [];

    if (!obj || typeof obj !== 'object') {
      errors.push({ path: 'strategy', message: 'Expected object' });
      return errors;
    }

    const record = obj as Record<string, unknown>;
    requireString(record, 'name', errors);
    requireString(record, 'version', errors, /^\d+\.\d+\.\d+$/);
    requireEnum(record, 'mode', ['serial', 'parallel'], errors);

    const limits = readObject(record, 'limits', errors);
    if (limits) {
      requireNumber(limits, 'limits.iterations', 'iterations', 1, 100, errors);
      requireNumber(limits, 'limits.maxParallelTasks', 'maxParallelTasks', 1, 50, errors);
      requireNumber(limits, 'limits.readyTimeoutSeconds', 'readyTimeoutSeconds', 1, 3600, errors);
      requireNumber(limits, 'limits.claimTimeoutSeconds', 'claimTimeoutSeconds', 1, 3600, errors);
      requireNumber(limits, 'limits.phaseTimeoutSeconds', 'phaseTimeoutSeconds', 1, 86400, errors);
      requireNumber(limits, 'limits.completeTimeoutSeconds', 'completeTimeoutSeconds', 1, 3600, errors);
      requireNumber(limits, 'limits.taskTimeoutSeconds', 'taskTimeoutSeconds', 1, 86400, errors);
    }

    const observability = readObject(record, 'observability', errors);
    if (observability) {
      requireBoolean(observability, 'observability.forever', 'forever', errors);
      requireBoolean(observability, 'observability.showOutput', 'showOutput', errors);
    }

    return errors;
  }

  useEffect(() => {
    if (!initialStrategy || isDirty || initialStrategy === content) return;
    setContent(initialStrategy);
    const validationErrors = validateJson(initialStrategy);
    setErrors(validationErrors);
    setIsValid(validationErrors.length === 0);
  }, [content, initialStrategy, isDirty, validateJson]);

  function readObject(
    record: Record<string, unknown>,
    key: string,
    errors: ValidationError[],
  ): Record<string, unknown> | null {
    const value = record[key];
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      errors.push({ path: key, message: 'Expected object' });
      return null;
    }
    return value as Record<string, unknown>;
  }

  function requireString(
    record: Record<string, unknown>,
    key: string,
    errors: ValidationError[],
    pattern?: RegExp,
  ): void {
    const value = record[key];
    if (typeof value !== 'string' || !value.trim()) {
      errors.push({ path: key, message: 'Expected non-empty string' });
      return;
    }
    if (pattern && !pattern.test(value)) {
      errors.push({ path: key, message: 'Expected semantic version, for example 1.0.0' });
    }
  }

  function requireEnum(
    record: Record<string, unknown>,
    key: string,
    allowed: string[],
    errors: ValidationError[],
  ): void {
    const value = record[key];
    if (typeof value !== 'string' || !allowed.includes(value)) {
      errors.push({ path: key, message: `Expected one of: ${allowed.join(', ')}` });
    }
  }

  function requireNumber(
    record: Record<string, unknown>,
    path: string,
    key: string,
    min: number,
    max: number,
    errors: ValidationError[],
  ): void {
    const value = record[key];
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      errors.push({ path, message: 'Expected finite number' });
      return;
    }
    if (value < min || value > max) {
      errors.push({ path, message: `Expected value between ${min} and ${max}` });
    }
  }

  function requireBoolean(
    record: Record<string, unknown>,
    path: string,
    key: string,
    errors: ValidationError[],
  ): void {
    if (typeof record[key] !== 'boolean') {
      errors.push({ path, message: 'Expected boolean' });
    }
  }

  const handleEditorChange = useCallback((value: string | undefined) => {
    if (!value) return;

    setContent(value);
    setIsDirty(true);

    const validationErrors = validateJson(value);
    setErrors(validationErrors);
    setIsValid(validationErrors.length === 0);

    if (onValidate) {
      onValidate(validationErrors.length === 0, validationErrors);
    }
  }, [validateJson, onValidate]);

  const handleTemplateSelect = useCallback((templateId: string) => {
    const template = templates.find(t => t.id === templateId);
    if (template) {
      const newContent = JSON.stringify(template.content, null, 2);
      setContent(newContent);
      setSelectedTemplate(templateId);
      setIsDirty(true);

      const validationErrors = validateJson(newContent);
      setErrors(validationErrors);
      setIsValid(validationErrors.length === 0);
    }
  }, [templates, validateJson]);

  const handleSave = useCallback(async () => {
    if (!isValid || saveState === 'saving') return;

    try {
      const parsed = JSON.parse(content) as DirectorExecutionStrategy;
      if (onSave) {
        await onSave(parsed);
      }
      setIsDirty(false);
    } catch {
      // JSON parse errors are already surfaced by validation. Async save errors
      // are shown by the parent panel through saveMessage.
    }
  }, [content, isValid, onSave, saveState]);

  const handleReset = useCallback(() => {
    setContent(JSON.stringify(DEFAULT_STRATEGY, null, 2));
    setSelectedTemplate('default');
    setIsDirty(false);
    setIsValid(true);
    setErrors([]);
  }, []);

  const handleCopy = useCallback(() => {
    void navigator.clipboard?.writeText(content);
  }, [content]);

  const editorOptions = useMemo(() => ({
    minimap: { enabled: false },
    fontSize: 13,
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
    lineNumbers: 'on' as const,
    scrollBeyondLastLine: false,
    automaticLayout: true,
    tabSize: 2,
    wordWrap: 'on' as const,
    readOnly,
    padding: { top: 16, bottom: 16 },
    renderLineHighlight: 'all' as const,
    scrollbar: {
      vertical: 'auto' as const,
      horizontal: 'auto' as const,
    },
  }), [readOnly]);

  return (
    <div
      data-testid="strategy-editor-panel"
      className="h-full flex flex-col bg-[linear-gradient(165deg,rgba(15,23,42,0.96),rgba(30,27,75,0.78),rgba(8,15,31,0.98))]"
    >
      <div className="flex min-h-14 items-center justify-between gap-3 border-b border-indigo-400/20 px-4 py-2">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-indigo-500/[0.15] border border-indigo-400/25 flex items-center justify-center shadow-lg shadow-indigo-500/10">
            <Code2 className="w-4 h-4 text-indigo-200" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-indigo-100">执行策略</h2>
            <p className="text-[10px] text-indigo-300/70 uppercase tracking-wider">Director Settings JSON</p>
          </div>
        </div>

        <div className="flex min-w-0 items-center gap-2">
          {saveMessage ? (
            <div
              className={cn(
                'hidden max-w-[260px] truncate rounded border px-2 py-1 text-[11px] md:block',
                saveState === 'error'
                  ? 'border-red-500/25 bg-red-500/10 text-red-200'
                  : saveState === 'saved'
                    ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-200'
                    : 'border-white/10 bg-white/5 text-slate-300',
              )}
              data-testid="strategy-editor-save-message"
              title={saveMessage}
            >
              {saveMessage}
            </div>
          ) : null}
          {isValid ? (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
              <span className="text-xs text-emerald-400">有效</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/20">
              <AlertCircle className="w-3.5 h-3.5 text-red-400" />
              <span className="text-xs text-red-400">{errors.length} 个错误</span>
            </div>
          )}

          <div className="w-px h-6 bg-white/10 mx-1" />

          <Button
            variant="outline"
            size="sm"
            onClick={handleCopy}
            className="border-slate-500/40 text-slate-300 hover:bg-white/5"
          >
            <Copy className="w-3.5 h-3.5 mr-1.5" />
            复制
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            className="border-slate-500/40 text-slate-300 hover:bg-white/5"
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            重置
          </Button>

          <Button
            size="sm"
            onClick={() => { void handleSave(); }}
            disabled={!isValid || !isDirty || saveState === 'saving'}
            data-testid="strategy-editor-save"
            className={cn(
              'bg-emerald-500/[0.15] border border-emerald-400/30 text-emerald-100 hover:bg-emerald-500/25',
              (!isValid || !isDirty || saveState === 'saving') && 'opacity-50 cursor-not-allowed'
            )}
          >
            <Save className="w-3.5 h-3.5 mr-1.5" />
            {saveState === 'saving' ? '保存中' : saveButtonLabel}
          </Button>
        </div>
      </div>

      <div className="flex min-h-10 items-center justify-between gap-3 border-b border-indigo-400/10 bg-indigo-500/5 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <SlidersHorizontal className="w-3.5 h-3.5 text-cyan-300/80" />
          <span className="text-xs text-slate-300/70">模板</span>
          <div className="flex gap-1">
            {templates.map(template => (
              <button
                key={template.id}
                onClick={() => handleTemplateSelect(template.id)}
                data-testid={`strategy-template-${template.id}`}
                className={cn(
                  'px-2 py-1 rounded text-[10px] transition-all',
                  selectedTemplate === template.id
                    ? 'bg-indigo-500/20 text-indigo-100 border border-indigo-400/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                )}
              >
                {template.name}
              </button>
            ))}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2 text-[10px] text-slate-400">
          <FileJson className="w-3 h-3" />
          <span>Backend settings mapper</span>
          {isDirty && <span className="text-cyan-300">•</span>}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {isMonacoReady ? (
          <Editor
            height="100%"
            defaultLanguage="json"
            value={content}
            onChange={handleEditorChange}
            theme="vs-dark"
            options={editorOptions}
          />
        ) : monacoLoadFailed ? (
          <textarea
            data-testid="strategy-json-editor-fallback"
            className="h-full w-full resize-none bg-slate-950 p-4 font-mono text-[12px] leading-relaxed text-slate-200 outline-none"
            value={content}
            onChange={(event) => handleEditorChange(event.currentTarget.value)}
            readOnly={readOnly}
            spellCheck={false}
          />
        ) : (
          <div className="flex h-full items-center justify-center bg-slate-950/70 text-xs text-slate-400">
            正在加载本地策略编辑器...
          </div>
        )}
      </div>

      {errors.length > 0 && (
        <div className="h-32 border-t border-red-400/20 bg-red-950/20 overflow-auto">
          <div className="p-3 space-y-1">
            <div className="text-[10px] uppercase tracking-wider text-red-400/70 mb-2">
              验证错误 ({errors.length})
            </div>
            {errors.map((error, index) => (
              <div key={index} className="flex items-start gap-2 text-xs">
                <AlertCircle className="w-3 h-3 text-red-400 mt-0.5 flex-shrink-0" />
                <span className="text-red-300 font-mono">{error.path}</span>
                <span className="text-red-400/70">:</span>
                <span className="text-red-400">{error.message}</span>
                {error.line && (
                  <span className="text-red-500/50 ml-auto">Line {error.line}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
