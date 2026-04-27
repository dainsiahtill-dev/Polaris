import { 
  CheckCircle2, 
  AlertTriangle, 
  Loader2, 
  PlayCircle, 
  ChevronDown,
  Settings
} from 'lucide-react';
import { useState } from 'react';
import type { SimpleProvider } from './types';

export interface SimpleRole {
  role: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';
  providerId?: string;
  status: 'unconfigured' | 'ready' | 'failed' | 'degraded';
  lastTest?: {
    at: string;
    result: 'pass' | 'fail';
    reason?: string;
  };
}

interface SimpleRoleCardProps {
  role: SimpleRole;
  availableProviders: SimpleProvider[];
  onUpdate: (updates: Partial<SimpleRole>) => void;
  onTestRole: () => Promise<void>;
  onViewTestReport?: () => void;
}

const ROLE_META: Record<string, { label: string; color: string; badge: string; description: string }> = {
  pm: {
    label: 'PM',
    color: 'text-cyan-300',
    badge: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
    description: '总领全局，统筹任务与节奏。'
  },
  director: {
    label: 'Director',
    color: 'text-emerald-300',
    badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
    description: '负责实现、调度与技术裁断（实际编码）。'
  },
  chief_engineer: {
    label: 'Chief Engineer',
    color: 'text-emerald-400',
    badge: 'bg-emerald-600/20 text-emerald-300 border-emerald-600/30',
    description: '绘制技术蓝图，定体例与纲目（设计不编码）。'
  },
  qa: {
    label: 'QA',
    color: 'text-blue-200',
    badge: 'bg-blue-500/20 text-blue-200 border-blue-500/30',
    description: '掌审核复核，稽核质量与风险。'
  },
  architect: {
    label: 'Architect',
    color: 'text-amber-300',
    badge: 'bg-amber-500/20 text-amber-200 border-amber-500/30',
    description: '草拟项目规格与架构文档，定体例与纲目。'
  },
  cfo: {
    label: 'CFO',
    color: 'text-purple-300',
    badge: 'bg-purple-500/20 text-purple-200 border-purple-500/30',
    description: '核算预算，监控Token用量与成本。'
  },
  hr: {
    label: 'HR',
    color: 'text-pink-300',
    badge: 'bg-pink-500/20 text-pink-200 border-pink-500/30',
    description: '管理LLM配置与模型任免。'
  },
};

const STATUS_COLORS = {
  unconfigured: 'text-gray-400',
  ready: 'text-emerald-400',
  failed: 'text-red-400',
  degraded: 'text-amber-400'
};

const STATUS_BADGES = {
  unconfigured: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  ready: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  failed: 'bg-red-500/20 text-red-200 border-red-500/30',
  degraded: 'bg-amber-500/20 text-amber-200 border-amber-500/30'
};

const STATUS_LABELS = {
  unconfigured: '未设',
  ready: '就绪',
  failed: '失准',
  degraded: '降级',
};

const ROLE_TEST_DESCRIPTIONS: Record<string, string> = {
  pm: '检验结构化任务输出与验收条款（含 JSON 解析）。',
  director: '检验证据与执行指令输出（不直接生成补丁）。',
  chief_engineer: '检验蓝图设计与体例规划的完整性。',
  qa: '检验 PASS/FAIL Reject结论与理由完整性。',
  architect: '检验 spec.md 草拟质量与结构完整度。',
  cfo: '检验预算核算与 Token 用量监控能力。',
  hr: '检验 LLM 配置与模型管理能力。'
};

export function SimpleRoleCard({
  role,
  availableProviders,
  onUpdate,
  onTestRole,
  onViewTestReport
}: SimpleRoleCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  const meta = ROLE_META[role.role];
  const readyProviders = availableProviders.filter(p => p.status === 'ready');
  const selectedProvider = availableProviders.find(p => p.id === role.providerId);

  const handleProviderChange = (providerId: string) => {
    onUpdate({ providerId, status: 'unconfigured' });
  };

  const handleRunRoleTest = async () => {
    setIsTesting(true);
    try {
      await onTestRole();
    } finally {
      setIsTesting(false);
    }
  };

  const renderStatusIndicator = () => {
    switch (role.status) {
      case 'ready':
        return <CheckCircle2 className="size-4 text-emerald-400" />;
      case 'failed':
        return <AlertTriangle className="size-4 text-red-400" />;
      case 'degraded':
        return <AlertTriangle className="size-4 text-amber-400" />;
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
            <h4 className="text-sm font-semibold text-text-main">{meta.label}</h4>
            <p className="text-[10px] text-text-dim">{meta.description}</p>
          </div>
        </div>
        
        <div className="flex items-center gap-2">
          <button
            onClick={handleRunRoleTest}
            disabled={isTesting || !role.providerId || role.status === 'degraded'}
            className="px-3 py-1.5 text-[10px] font-semibold bg-purple-500/80 hover:bg-purple-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1"
          >
            {isTesting ? (
              <Loader2 className="size-3 animate-spin" />
            ) : (
              <PlayCircle className="size-3" />
            )}
            试运行
          </button>

          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="p-1.5 rounded border border-white/10 hover:border-accent/40 transition-colors"
          >
            {isExpanded ? <ChevronDown className="size-3 rotate-180" /> : <ChevronDown className="size-3" />}
          </button>
        </div>
      </div>

      {/* Provider Selection */}
      <div className="flex items-center gap-3">
        <label className="text-xs text-text-muted">模型:</label>
        <select
          value={role.providerId || ''}
          onChange={(e) => handleProviderChange(e.target.value)}
          disabled={readyProviders.length === 0}
          className="flex-1 bg-black/30 text-text-main px-3 py-2 rounded border border-white/10 text-sm focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/50 disabled:opacity-60"
        >
          <option value="">请选择模型...</option>
          {readyProviders.map(provider => (
            <option key={provider.id} value={provider.id}>
              {provider.name} ({provider.modelId})
            </option>
          ))}
        </select>
      </div>

      {/* Status Messages */}
      {readyProviders.length === 0 && (
        <div className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-2">
          暂无可用模型，请先在第一步完成配置并通过测试。
        </div>
      )}

      {role.status === 'degraded' && selectedProvider && (
        <div className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-2">
          已分配模型“{selectedProvider.name}”当前非就绪。
          请先在第一步修复，或改配其他模型。
        </div>
      )}

      {role.status === 'failed' && role.lastTest?.reason && (
        <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2">
          角色试运行失败: {role.lastTest.reason}
        </div>
      )}
    </div>
  );

  const renderExpandedView = () => (
    <div className="space-y-4 pt-4 border-t border-white/10">
      {/* Selected Provider Details */}
      {selectedProvider && (
        <div className="space-y-3">
          <h5 className="text-xs font-semibold text-text-main">已选模型</h5>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">名称:</span>
              <span className="text-text-main">{selectedProvider.name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">类型:</span>
              <span className="text-text-main capitalize">{selectedProvider.kind}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">模型 ID:</span>
              <span className="text-text-main font-mono">{selectedProvider.modelId}</span>
            </div>
            {selectedProvider.lastTest?.latencyMs && (
              <div className="flex justify-between">
                <span className="text-text-muted">时延:</span>
                <span className="text-text-main">{selectedProvider.lastTest.latencyMs}ms</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Role Test Details */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-text-main">角色试运行</h5>
        <div className="text-xs text-text-dim">
          {ROLE_TEST_DESCRIPTIONS[role.role]}
        </div>
        
        {role.lastTest && (
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-text-muted">最近测试:</span>
              <span className="text-text-main">{new Date(role.lastTest.at).toLocaleString()}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-text-muted">结果:</span>
              <span className={`font-semibold ${
                role.lastTest.result === 'pass' ? 'text-emerald-400' : 'text-red-400'
              }`}>
                {role.lastTest.result === 'pass' ? '通过' : '失败'}
              </span>
            </div>
            {role.lastTest.reason && (
              <div className="text-text-main">{role.lastTest.reason}</div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 pt-3 border-t border-white/10">
        <button
          onClick={handleRunRoleTest}
          disabled={isTesting || !role.providerId || role.status === 'degraded'}
          className="px-3 py-1.5 text-[10px] font-semibold bg-purple-500/80 hover:bg-purple-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1"
        >
          {isTesting ? (
            <Loader2 className="size-3 animate-spin" />
          ) : (
            <PlayCircle className="size-3" />
          )}
          试运行
        </button>
        
        {onViewTestReport && role.lastTest && (
          <button
            onClick={onViewTestReport}
            className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1"
          >
            <Settings className="size-3" />
            查看回执
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div className="bg-white/5 rounded-xl p-4 border border-white/10 hover:border-white/20 transition-all">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${meta.badge}`}>
            {meta.label}
          </span>
          <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${STATUS_BADGES[role.status]}`}>
            {STATUS_LABELS[role.status]}
          </span>
        </div>
      </div>

      {/* Content */}
      <>
        {renderCompactView()}
        {isExpanded && renderExpandedView()}
      </>
    </div>
  );
}
