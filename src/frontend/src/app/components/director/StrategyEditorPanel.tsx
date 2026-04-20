/** StrategyEditorPanel - 策略编辑器面板
 *
 * 功能：
 * - Monaco Editor 代码编辑
 * - JSON Schema 验证
 * - 策略模板选择
 * - 实时语法检查
 */
import { useState, useCallback, useMemo } from 'react';
import Editor from '@monaco-editor/react';
import {
  FileJson,
  CheckCircle2,
  AlertCircle,
  Save,
  RefreshCw,
  Copy,
  Code2,
  Sparkles,
  History,
} from 'lucide-react';
import { Button } from '@/app/components/ui/button';
import { cn } from '@/app/components/ui/utils';

// 策略 JSON Schema 定义
const STRATEGY_SCHEMA = {
  type: 'object',
  properties: {
    name: { type: 'string', minLength: 1 },
    version: { type: 'string', pattern: '^\\d+\\.\\d+\\.\\d+$' },
    config: {
      type: 'object',
      properties: {
        maxIterations: { type: 'number', minimum: 1, maximum: 100 },
        timeout: { type: 'number', minimum: 1000 },
        retryOnFailure: { type: 'boolean' },
        parallelExecution: { type: 'boolean' },
        autoRollback: { type: 'boolean' },
      },
    },
    rules: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          name: { type: 'string' },
          enabled: { type: 'boolean' },
          priority: { type: 'number' },
          action: { type: 'string', enum: ['approve', 'reject', 'warn', 'skip'] },
          conditions: { type: 'array' },
        },
        required: ['id', 'name', 'action'],
      },
    },
    notifications: {
      type: 'array',
      items: { type: 'string', enum: ['email', 'slack', 'webhook', 'desktop'] },
    },
  },
  required: ['name', 'version', 'config'],
};

// 默认策略模板
const DEFAULT_STRATEGY = {
  name: 'default-strategy',
  version: '1.0.0',
  config: {
    maxIterations: 10,
    timeout: 300000,
    retryOnFailure: true,
    parallelExecution: false,
    autoRollback: false,
  },
  rules: [
    {
      id: 'rule-1',
      name: '安全检查',
      enabled: true,
      priority: 1,
      action: 'reject',
      conditions: ['security_scan'],
    },
    {
      id: 'rule-2',
      name: '测试通过',
      enabled: true,
      priority: 2,
      action: 'approve',
      conditions: ['test_coverage > 80'],
    },
  ],
  notifications: ['desktop'],
};

interface ValidationError {
  path: string;
  message: string;
  line?: number;
}

interface StrategyEditorPanelProps {
  initialStrategy?: string;
  onSave?: (strategy: object) => void;
  onValidate?: (isValid: boolean, errors: ValidationError[]) => void;
  readOnly?: boolean;
}

export function StrategyEditorPanel({
  initialStrategy,
  onSave,
  onValidate,
  readOnly = false,
}: StrategyEditorPanelProps) {
  const [content, setContent] = useState<string>(
    initialStrategy || JSON.stringify(DEFAULT_STRATEGY, null, 2)
  );
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [isValid, setIsValid] = useState(true);
  const [isDirty, setIsDirty] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState('default');

  const templates = useMemo(() => [
    { id: 'default', name: '默认策略', content: DEFAULT_STRATEGY },
    {
      id: 'strict',
      name: '严格模式',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'strict-strategy',
        config: { ...DEFAULT_STRATEGY.config, maxIterations: 5, autoRollback: true },
      },
    },
    {
      id: 'fast',
      name: '快速执行',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'fast-strategy',
        config: { ...DEFAULT_STRATEGY.config, maxIterations: 20, parallelExecution: true, timeout: 60000 },
      },
    },
    {
      id: 'safe',
      name: '安全优先',
      content: {
        ...DEFAULT_STRATEGY,
        name: 'safe-strategy',
        rules: [
          ...DEFAULT_STRATEGY.rules,
          { id: 'rule-3', name: '代码审查', enabled: true, priority: 0, action: 'approve', conditions: ['code_review_approved'] },
        ],
        notifications: ['desktop', 'email', 'slack'],
      },
    },
  ], []);

  // JSON Schema 验证
  const validateJson = useCallback((jsonString: string): ValidationError[] => {
    const validationErrors: ValidationError[] = [];

    // 1. 语法检查
    try {
      const parsed = JSON.parse(jsonString);

      // 2. Schema 验证
      const schemaErrors = validateSchema(parsed, STRATEGY_SCHEMA);
      validationErrors.push(...schemaErrors);
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

  // Schema 验证辅助函数
  function validateSchema(obj: unknown, schema: object, path = ''): ValidationError[] {
    const errors: ValidationError[] = [];
    const s = schema as Record<string, unknown>;

    if (!obj || typeof obj !== 'object') {
      errors.push({ path, message: 'Invalid type' });
      return errors;
    }

    const objRecord = obj as Record<string, unknown>;

    // Check required fields
    if (Array.isArray(s.required)) {
      for (const field of s.required) {
        if (!(field in objRecord)) {
          errors.push({ path: `${path}.${field}`, message: `Missing required field: ${field}` });
        }
      }
    }

    // Check properties
    if (s.properties && typeof s.properties === 'object') {
      for (const [key, propSchema] of Object.entries(s.properties as object)) {
        if (key in objRecord) {
          const value = objRecord[key];
          const prop = propSchema as Record<string, unknown>;

          // Type check
          if (prop.type) {
            const actualType = Array.isArray(value) ? 'array' : typeof value;
            if (actualType !== prop.type && !(prop.type === 'number' && typeof value === 'number')) {
              errors.push({ path: `${path}.${key}`, message: `Expected ${prop.type}, got ${actualType}` });
            }
          }

          // Enum check
          if (prop.enum && Array.isArray(prop.enum) && !prop.enum.includes(value)) {
            errors.push({ path: `${path}.${key}`, message: `Value must be one of: ${(prop.enum as string[]).join(', ')}` });
          }

          // Range check
          if (prop.minimum !== undefined && typeof value === 'number' && value < (prop.minimum as number)) {
            errors.push({ path: `${path}.${key}`, message: `Value must be >= ${prop.minimum}` });
          }
          if (prop.maximum !== undefined && typeof value === 'number' && value > (prop.maximum as number)) {
            errors.push({ path: `${path}.${key}`, message: `Value must be <= ${prop.maximum}` });
          }
        }
      }
    }

    return errors;
  }

  // 处理内容变更
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

  // 处理模板选择
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

  // 保存策略
  const handleSave = useCallback(() => {
    if (!isValid) return;

    try {
      const parsed = JSON.parse(content);
      if (onSave) {
        onSave(parsed);
      }
      setIsDirty(false);
    } catch {
      // Ignore - already validated
    }
  }, [content, isValid, onSave]);

  // 重置为默认
  const handleReset = useCallback(() => {
    setContent(JSON.stringify(DEFAULT_STRATEGY, null, 2));
    setSelectedTemplate('default');
    setIsDirty(false);
    setIsValid(true);
    setErrors([]);
  }, []);

  // 复制到剪贴板
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(content);
  }, [content]);

  // Monaco Editor 配置
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
    <div className="h-full flex flex-col bg-[linear-gradient(165deg,rgba(50,35,18,0.40),rgba(28,18,48,0.65),rgba(14,20,40,0.80))]">
      {/* Header */}
      <div className="h-14 flex items-center justify-between px-4 border-b border-amber-400/20">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center shadow-lg shadow-amber-500/20">
            <Code2 className="w-4 h-4 text-amber-100" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-amber-100">策略编辑</h2>
            <p className="text-[10px] text-amber-400/60 uppercase tracking-wider">Strategy Editor</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* 验证状态 */}
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

          <div className="w-px h-6 bg-amber-400/20 mx-2" />

          <Button
            variant="outline"
            size="sm"
            onClick={handleCopy}
            className="border-amber-400/30 text-amber-400 hover:bg-amber-500/10"
          >
            <Copy className="w-3.5 h-3.5 mr-1.5" />
            复制
          </Button>

          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            className="border-amber-400/30 text-amber-400 hover:bg-amber-500/10"
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            重置
          </Button>

          <Button
            size="sm"
            onClick={handleSave}
            disabled={!isValid || !isDirty}
            className={cn(
              'bg-gradient-to-r from-amber-500/20 to-amber-600/20 border border-amber-400/30 text-amber-100 hover:from-amber-500/30 hover:to-amber-600/30',
              (!isValid || !isDirty) && 'opacity-50 cursor-not-allowed'
            )}
          >
            <Save className="w-3.5 h-3.5 mr-1.5" />
            保存
          </Button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="h-10 flex items-center justify-between px-4 border-b border-amber-400/10 bg-amber-500/5">
        <div className="flex items-center gap-2">
          <Sparkles className="w-3.5 h-3.5 text-amber-400/70" />
          <span className="text-xs text-amber-200/60">模板:</span>
          <div className="flex gap-1">
            {templates.map(template => (
              <button
                key={template.id}
                onClick={() => handleTemplateSelect(template.id)}
                className={cn(
                  'px-2 py-1 rounded text-[10px] transition-all',
                  selectedTemplate === template.id
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-400/30'
                    : 'text-amber-200/50 hover:text-amber-200/80 hover:bg-white/5'
                )}
              >
                {template.name}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 text-[10px] text-amber-200/50">
          <FileJson className="w-3 h-3" />
          <span>JSON Schema</span>
          {isDirty && <span className="text-amber-400">•</span>}
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 overflow-hidden">
        <Editor
          height="100%"
          defaultLanguage="json"
          value={content}
          onChange={handleEditorChange}
          theme="vs-dark"
          options={editorOptions}
        />
      </div>

      {/* Error Panel */}
      {errors.length > 0 && (
        <div className="h-32 border-t border-amber-400/20 bg-red-950/20 overflow-auto">
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
