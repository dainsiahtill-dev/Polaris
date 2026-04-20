import { 
  CheckCircle2, 
  AlertTriangle, 
  Loader2, 
  PlayCircle, 
  Trash2, 
  Edit3, 
  ChevronDown, 
  ChevronUp,
  Terminal,
  Clock,
  UserCheck,
  UserX,
  HelpCircle,
  Zap,
  Shield,
  Key,
  Eye
} from 'lucide-react';
import { useState, type ReactNode, useMemo } from 'react';
import {
  PROVIDER_LABELS,
  STATUS_BADGES,
  INTERVIEW_BADGES,
  INTERVIEW_STATUS,
  isCLIProvider,
  isCodexCLIProvider,
  isCLIConnection,
  isHTTPConnection,
  type ProviderKind,
  type SimpleProvider,
  type InterviewStatus
} from './types';

interface SimpleModelCardProps {
  provider: SimpleProvider;
  onUpdate: (updates: Partial<SimpleProvider>) => void;
  onDelete: () => void;
  onTest: () => void;
  renderModelBrowser?: (props: { modelId: string; onSelect: (modelId: string) => void }) => ReactNode;
  onOpenTuiBrowser?: () => void;
  onViewTestReport?: () => void;
}

export function SimpleModelCard({
  provider,
  onUpdate,
  onDelete,
  onTest,
  renderModelBrowser,
  onOpenTuiBrowser,
  onViewTestReport
}: SimpleModelCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<SimpleProvider>(provider);

  const isCodexCli = isCodexCLIProvider(provider.kind, provider.conn);
  const cliMode = provider.cliMode || 'headless';

  const usesOutputPath =
    isCLIConnection(provider.conn) &&
    (provider.conn.args || []).some((arg: string) => arg.includes('{output}'));

  const applyCodexPreset = () => {
    setEditForm((prev) => ({
      ...prev,
      name: prev.name && prev.name.trim() ? prev.name : 'Codex CLI',
      kind: 'codex_cli',
      cliMode: 'headless',
      conn: {
        kind: 'codex_cli',
        command: 'codex',
        args: ['exec', '--skip-git-repo-check', '--color', 'never', '--model', '{model}', '--json', '{prompt}'],
        env: (prev.conn.kind === 'codex_cli' || prev.conn.kind === 'gemini_cli') ? prev.conn.env : {}
      }
    }));
  };

  const getInterviewIcon = (status?: InterviewStatus) => {
    switch (status) {
      case INTERVIEW_STATUS.PASSED:
        return <UserCheck className="size-3 text-green-400" />;
      case INTERVIEW_STATUS.FAILED:
        return <UserX className="size-3 text-red-400" />;
      default:
        return <HelpCircle className="size-3 text-gray-400" />;
    }
  };

  const getInterviewLabel = (status?: InterviewStatus): string => {
    switch (status) {
      case INTERVIEW_STATUS.PASSED:
        return '面试通过';
      case INTERVIEW_STATUS.FAILED:
        return '面试失败';
      default:
        return '未测试';
    }
  };

  const providerType = useMemo(() => {
    if (isCLIConnection(provider.conn)) {
      return cliMode === 'tui' ? 'TUI' : 'CLI';
    }
    return 'HTTP';
  }, [provider.conn, cliMode]);

  const authType = useMemo(() => {
    if (isCLIConnection(provider.conn)) {
      return '无';
    }
    if (provider.conn.kind === 'http' && provider.conn.apiKey) {
      return 'API 密钥';
    }
    return '无';
  }, [provider.conn]);

  const providerFeatures = useMemo(() => {
    const features: string[] = [];
    if (isCLIProvider(provider.kind)) {
      features.push('CLI');
      if (cliMode === 'tui') {
        features.push('TUI');
      }
    }
    if (isHTTPConnection(provider.conn)) {
      features.push('REST API');
    }
    if (provider.costClass === 'LOCAL') {
      features.push('本地');
    }
    if (provider.costClass === 'METERED') {
      features.push('按量');
    }
    return features;
  }, [provider.kind, provider.conn, cliMode, provider.costClass]);

  const handleSaveEdit = () => {
    onUpdate(editForm);
    setIsEditing(false);
  };

  const handleCancelEdit = () => {
    setEditForm(provider);
    setIsEditing(false);
  };

  const renderStatusIndicator = () => {
    switch (provider.status) {
      case 'ready':
        return <CheckCircle2 className="size-4 text-emerald-400" />;
      case 'testing':
        return <Loader2 className="size-4 text-blue-400 animate-spin" />;
      case 'failed':
        return <AlertTriangle className="size-4 text-red-400" />;
      default:
        return <div className="size-4 rounded-full bg-gray-500/60" />;
    }
  };

  const renderCompactView = () => (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {renderStatusIndicator()}
          <div>
            <h4 className="text-sm font-semibold text-text-main">{provider.name}</h4>
            <div className="flex items-center gap-2 text-[10px] text-text-dim">
              <span className="font-mono">{provider.modelId || "默认"}</span>
              {provider.costClass && (
                <>
                  <span>•</span>
                  <span className="text-amber-400">{provider.costClass}</span>
                </>
              )}
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 px-2 py-1 rounded border border-white/10 bg-white/5">
            {getInterviewIcon(provider.interviewStatus)}
            <span className="text-[10px] text-text-main">
              {getInterviewLabel(provider.interviewStatus)}
            </span>
          </div>
          
          <button
            onClick={onTest}
            disabled={provider.status === 'testing'}
            className="px-3 py-1.5 text-[10px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1"
          >
            {provider.status === 'testing' ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <PlayCircle className="size-3" />
            )}
            Test
          </button>
          
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 transition-colors"
          >
            {isExpanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
          </button>
        </div>
      </div>

      {provider.lastError && (
        <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2">
          {provider.lastError}
        </div>
      )}
    </div>
  );

  const renderExpandedView = () => (
    <div className="space-y-4 pt-4 border-t border-white/10">
      <div className="grid grid-cols-3 gap-3">
        <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
          <Zap className="size-3.5 text-amber-400" />
          <div className="flex-1 min-w-0">
            <div className="text-[9px] text-text-dim uppercase tracking-wide">类型</div>
            <div className="text-xs text-text-main truncate">{providerType}</div>
          </div>
        </div>
        
        <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
          <Key className="size-3.5 text-cyan-400" />
          <div className="flex-1 min-w-0">
            <div className="text-[9px] text-text-dim uppercase tracking-wide">认证</div>
            <div className="text-xs text-text-main truncate">{authType}</div>
          </div>
        </div>
        
        <div className="flex items-center gap-2 px-3 py-2 rounded border border-white/10 bg-white/5">
          <Shield className="size-3.5 text-green-400" />
          <div className="flex-1 min-w-0">
            <div className="text-[9px] text-text-dim uppercase tracking-wide">特性</div>
            <div className="text-xs text-text-main truncate">{providerFeatures.join(', ') || '-'}</div>
          </div>
        </div>
      </div>

      {provider.interviewStatus && (
        <div className="space-y-3">
          <h5 className="text-xs font-semibold text-text-main flex items-center gap-2">
            <UserCheck className="size-3.5 text-accent" />
            面试记录
          </h5>
          <div className="flex items-center gap-2">
            <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${INTERVIEW_BADGES[provider.interviewStatus]}`}>
              {provider.interviewStatus.toUpperCase()}
            </span>
            {provider.lastInterviewAt && (
              <span className="flex items-center gap-1 text-[10px] text-text-dim">
                <Clock className="size-3" />
                {new Date(provider.lastInterviewAt).toLocaleString()}
              </span>
            )}
          </div>
          {provider.interviewDetails?.role && (
            <div className="text-[10px] text-text-muted">
              角色: <span className="text-text-main">{provider.interviewDetails.role}</span>
            </div>
          )}
          {provider.interviewDetails?.runId && (
            <div className="text-[10px] text-text-muted">
              运行ID: <span className="text-text-main font-mono">{provider.interviewDetails.runId}</span>
            </div>
          )}
        </div>
      )}

      {provider.lastTest && (
        <div className="space-y-3">
          <h5 className="text-xs font-semibold text-text-main flex items-center gap-2">
            <Clock className="size-3.5 text-cyan-400" />
            上次测试
          </h5>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">时间:</span>
              <span className="text-text-main">{new Date(provider.lastTest.at).toLocaleString()}</span>
            </div>
            {provider.lastTest.latencyMs && (
              <div className="flex justify-between">
                <span className="text-text-muted">延迟:</span>
                <span className="text-text-main">{provider.lastTest.latencyMs}ms</span>
              </div>
            )}
            {provider.lastTest.usage && (
              <div className="flex justify-between">
                <span className="text-text-muted">令牌:</span>
                <span className="text-text-main">
                  {provider.lastTest.usage.totalTokens} {provider.lastTest.usage.estimated ? '(est.)' : ''}
                </span>
              </div>
            )}
            {provider.lastTest.note && (
              <div className="text-text-main">{provider.lastTest.note}</div>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 pt-3 border-t border-white/10">
        <button
          onClick={onTest}
          disabled={provider.status === 'testing'}
          className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-cyan-400/40 disabled:opacity-60 flex items-center gap-1"
        >
          <PlayCircle className="size-3" />
          测试
        </button>
        
        {isCLIConnection(provider.conn) && cliMode === 'tui' && onOpenTuiBrowser && (
          <button
            onClick={onOpenTuiBrowser}
            className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-cyan-400/40 flex items-center gap-1"
          >
            <Terminal className="size-3" />
            TUI 浏览器
          </button>
        )}
        
        {onViewTestReport && (
          <button
            onClick={onViewTestReport}
            className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1"
          >
            <Eye className="size-3" />
            查看报告
          </button>
        )}
        
        <button
          onClick={() => setIsEditing(true)}
          className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1"
        >
          <Edit3 className="size-3" />
          编辑
        </button>
        
        <button
          onClick={onDelete}
          className="px-3 py-1.5 text-[10px] border border-red-500/30 rounded hover:border-red-500/40 text-red-400 flex items-center gap-1"
        >
          <Trash2 className="size-3" />
          删除
        </button>
      </div>

      {isCLIConnection(provider.conn) && provider.conn.command && (
        <div className="space-y-2 text-xs border-t border-white/10 pt-4">
          <h5 className="text-xs font-semibold text-text-muted">命令</h5>
          <div className="font-mono text-[10px] text-text-main bg-black/30 rounded px-3 py-2 border border-white/10">
            {provider.conn.command} {(provider.conn.args || []).join(' ')}
          </div>
        </div>
      )}

      {isHTTPConnection(provider.conn) && provider.conn.baseUrl && (
        <div className="space-y-2 text-xs border-t border-white/10 pt-4">
          <h5 className="text-xs font-semibold text-text-muted">基础URL</h5>
          <div className="font-mono text-[10px] text-text-main bg-black/30 rounded px-3 py-2 border border-white/10 break-all">
            {provider.conn.baseUrl}
          </div>
        </div>
      )}
    </div>
  );

  const renderEditView = () => (
    <div className="space-y-4 pt-4 border-t border-white/10">
      <h5 className="text-xs font-semibold text-text-main">编辑提供商</h5>
      
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-text-muted mb-1">名称</label>
          <input
            type="text"
            value={editForm.name}
            onChange={(e) => setEditForm(prev => ({ ...prev, name: e.target.value }))}
            className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm"
          />
        </div>

        <div>
          <label className="block text-xs text-text-muted mb-1">类型</label>
          <select
            value={editForm.kind}
            onChange={(e) => {
              const newKind = e.target.value as ProviderKind;
              setEditForm((prev: SimpleProvider) => {
                const baseProvider = { ...prev, kind: newKind, cliMode: isCLIProvider(newKind) ? 'headless' : undefined };
                if (newKind === 'codex_cli' || newKind === 'gemini_cli') {
                  return {
                    ...baseProvider,
                    conn: { kind: newKind, command: '', args: [], env: {} }
                  } as SimpleProvider;
                } else {
                  return {
                    ...baseProvider,
                    conn: { kind: 'http', baseUrl: '' }
                  } as SimpleProvider;
                }
              });
            }}
            className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm"
          >
            <option value="codex_cli">Codex CLI</option>
            <option value="gemini_cli">Gemini CLI</option>
            <option value="ollama">Ollama</option>
            <option value="openai_compat">OpenAI 兼容</option>
            <option value="anthropic_compat">Anthropic 兼容</option>
            <option value="custom_https">自定义 HTTPS</option>
          </select>
        </div>

        {isCLIConnection(editForm.conn) ? (
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">CLI 模式</label>
              <select
                value={editForm.cliMode || 'headless'}
                onChange={(e) => setEditForm(prev => ({ ...prev, cliMode: e.target.value as 'tui' | 'headless' }))}
                className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm"
              >
                <option value="headless">静默执行（非交互）</option>
                <option value="tui">TUI（交互）</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">命令</label>
              <input
                type="text"
                value={editForm.conn.command}
                onChange={(e) => setEditForm(prev => ({ 
                  ...prev, 
                  conn: { ...prev.conn, kind: editForm.conn.kind, command: e.target.value }
                }) as SimpleProvider)}
                className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono"
                placeholder="例如 codex、gemini"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">参数（每行一项）</label>
              <textarea
                value={(editForm.conn.args || []).join('\n')}
                onChange={(e) => setEditForm(prev => ({ 
                  ...prev, 
                  conn: { ...prev.conn, kind: editForm.conn.kind, args: e.target.value.split('\n').filter(Boolean) }
                }) as SimpleProvider)}
                className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono h-16"
              />
            </div>
            {usesOutputPath && (
              <div>
                <label className="block text-xs text-text-muted mb-1">输出路径（可选）</label>
                <input
                  type="text"
                  value={editForm.outputPath || ""}
                  onChange={(e) => setEditForm(prev => ({ ...prev, outputPath: e.target.value }))}
                  className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono"
                  placeholder="runtime/CODEX_LAST_MESSAGE.md"
                />
                <p className="text-[9px] text-text-dim mt-1">仅当 args 中包含 {`{output}`} 时才会写入。</p>
              </div>
            )}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={applyCodexPreset}
                className="px-3 py-1.5 text-[10px] border border-emerald-500/30 rounded hover:border-emerald-400/60 text-emerald-200"
              >
                应用 Codex CLI 预设
              </button>
              <span className="text-[9px] text-text-dim">推荐用于 codex exec + 面试测试</span>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">基础 URL</label>
              <input
                type="text"
                value={isHTTPConnection(editForm.conn) ? editForm.conn.baseUrl : ''}
                onChange={(e) => setEditForm(prev => ({ 
                  ...prev, 
                  conn: { ...prev.conn, kind: 'http', baseUrl: e.target.value }
                }) as SimpleProvider)}
                className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono"
                placeholder="https://api.example.com/v1"
              />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-1">API 密钥</label>
              <input
                type="text"
                value={isHTTPConnection(editForm.conn) ? editForm.conn.apiKey || '' : ''}
                onChange={(e) => setEditForm(prev => ({ 
                  ...prev, 
                  conn: { ...prev.conn, kind: 'http', apiKey: e.target.value }
                }) as SimpleProvider)}
                className="w-full bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono"
                placeholder="请输入 API 密钥"
              />
            </div>
          </div>
        )}

        <div>
          <label className="block text-xs text-text-muted mb-1">模型 ID</label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={editForm.modelId}
              onChange={(e) => setEditForm(prev => ({ ...prev, modelId: e.target.value }))}
              className="flex-1 bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm font-mono"
              placeholder="例如 gpt-4、claude-3-5-sonnet"
            />
            {renderModelBrowser
              ? renderModelBrowser({
                  modelId: editForm.modelId,
                  onSelect: (value) => setEditForm((prev) => ({ ...prev, modelId: value })),
                })
              : null}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 pt-3 border-t border-white/10">
        <button
          onClick={handleSaveEdit}
          className="px-3 py-1.5 text-[10px] font-semibold bg-accent/80 hover:bg-accent text-white rounded transition-colors"
        >
          保存
        </button>
        <button
          onClick={handleCancelEdit}
          className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40"
        >
          取消
        </button>
      </div>
    </div>
  );

  return (
    <div className="bg-white/5 rounded-xl p-4 border border-white/10 hover:border-white/20 transition-all">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${STATUS_BADGES[provider.status]}`}>
            {provider.status.toUpperCase()}
          </span>
          <span className="text-[10px] text-text-dim capitalize">
            {PROVIDER_LABELS[provider.kind]}
          </span>
          {isCodexCli && (
            <span className="px-2 py-1 text-[9px] uppercase font-semibold rounded border bg-emerald-500/10 text-emerald-200 border-emerald-500/30">
              Codex CLI
            </span>
          )}
        </div>
      </div>

      {/* Content */}
      {isEditing ? renderEditView() : (
        <>
          {renderCompactView()}
          {isExpanded && renderExpandedView()}
        </>
      )}
    </div>
  );
}

