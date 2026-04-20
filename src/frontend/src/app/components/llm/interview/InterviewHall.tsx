import { CheckCircle2, AlertTriangle, PlayCircle, ShieldCheck, Loader2, Cpu, Zap, Info } from 'lucide-react';
import { useState } from 'react';
import { MultiRoleInterviewStatus, InterviewDetailsModal } from './MultiRoleInterviewStatus';

export interface InterviewRoleSummary {
  id: 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';
  label: string;
  description: string;
  requiresThinking: boolean;
  minConfidence: number;
  thinkingConfidence?: number | null;
  thinkingSupported?: boolean | null;
  candidate?: {
    providerId?: string;
    providerName?: string;
    model?: string;
  };
  readiness?: {
    ready?: boolean;
    grade?: string;
  };
}

export interface InterviewCandidateSummary {
  id: string;
  roleLabel: string;
  providerName: string;
  model: string;
  ready?: boolean;
  thinkingSupported?: boolean | null;
  thinkingConfidence?: number | null;
}

export interface ConnectivityResult {
  ok: boolean;
  timestamp: string;
  latencyMs?: number;
  error?: string;
  model?: string;
  sourceRole?: string;
  thinking?: {
    supportsThinking?: boolean;
    confidence?: number;
    format?: string;
  };
}

// 面试结果详情接口
export interface InterviewResultDetail {
  status: 'passed' | 'failed' | 'none';
  timestamp?: string;
  score?: number;
  lastRunId?: string;
  thinkingSupported?: boolean;
  thinkingConfidence?: number | null;
}

// 扩展后的面试提供商摘要接口
export interface InterviewProviderSummary {
  id: string;
  name: string;
  model: string;
  providerType: string;
  status: 'ready' | 'testing' | 'failed' | 'untested';
  thinkingSupported?: boolean;
  thinkingConfidence?: number | null;
  lastConnectivityTest?: {
    timestamp: string;
    success: boolean;
    latencyMs?: number;
    error?: string;
  };
  // 扩展：多角色面试结果（替代单一 interviewStatus）
  interviewResults?: Record<RoleId, InterviewResultDetail>;
  // 保持向后兼容：单一面试状态
  interviewStatus?: 'passed' | 'failed' | 'none';
  // 保持向后兼容：最后面试记录
  lastInterview?: {
    timestamp: string;
    status: 'passed' | 'failed';
    role?: string;
    model?: string;
  };
}

export type RoleId = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';

interface InterviewHallLegacyProps {
  roles: InterviewRoleSummary[];
  candidates: InterviewCandidateSummary[];
  selectedRole: RoleId;
  onSelectRole: (role: RoleId) => void;
  onStartInterview: () => void;
  onRunReadiness?: () => void;
  disabledReason?: string | null;
  running?: boolean;
}

interface InterviewHallV2Props {
  roles: InterviewRoleSummary[];
  providers: InterviewProviderSummary[];
  selectedRole: RoleId | null;
  selectedProvider: string | null;
  onSelectRole: (role: RoleId) => void;
  onSelectProvider: (providerId: string) => void;
  onRunConnectivityTest: (payload: { role: RoleId; providerId: string; model: string }) => void;
  onRunInterview: (payload: { role: RoleId; providerId: string; model: string }) => void;
  connectivityResults: Map<string, ConnectivityResult>;
  interviewRunning?: boolean;
  connectivityRunning?: boolean;
  onSkipConnectivityTest?: (role: RoleId, providerId: string) => void;
}

type InterviewHallProps = InterviewHallLegacyProps | InterviewHallV2Props;

const ROLE_BADGES: Record<string, string> = {
  pm: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
  director: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  qa: 'bg-blue-500/20 text-blue-200 border-blue-500/30',
  architect: 'bg-amber-500/20 text-amber-200 border-amber-500/30'
};

const STATUS_STYLES: Record<string, { border: string; bg: string; dot: string; text: string }> = {
  ready: {
    border: 'border-emerald-500/40',
    bg: 'bg-emerald-500/10',
    dot: 'bg-emerald-400',
    text: 'text-emerald-300'
  },
  failed: {
    border: 'border-rose-500/40',
    bg: 'bg-rose-500/10',
    dot: 'bg-rose-400',
    text: 'text-rose-300'
  },
  testing: {
    border: 'border-cyan-500/40',
    bg: 'bg-cyan-500/10',
    dot: 'bg-cyan-300',
    text: 'text-cyan-200'
  },
  untested: {
    border: 'border-white/10',
    bg: 'bg-white/5',
    dot: 'bg-white/40',
    text: 'text-text-dim'
  }
};

const STATUS_LABELS: Record<string, string> = {
  ready: '连通正常',
  failed: '连通失败',
  testing: '连通测试中',
  untested: '连通未测'
};

const formatTimestamp = (timestamp?: string) => {
  if (!timestamp) return '未测试';
  try {
    return new Date(timestamp).toLocaleString();
  } catch {
    return timestamp;
  }
};

function InterviewHallLegacy({
  roles,
  candidates,
  selectedRole,
  onSelectRole,
  onStartInterview,
  onRunReadiness,
  disabledReason,
  running
}: InterviewHallLegacyProps) {
  const activeRole = roles.find(role => role.id === selectedRole);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs text-text-dim uppercase tracking-wide">LLM 面试中枢</div>
          <h3 className="text-lg font-semibold text-text-main">面试大厅</h3>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-text-dim">
          <ShieldCheck className="size-4 text-emerald-300" />
          核心岗位须配思考型模型。
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1.2fr_1fr] gap-6">
        <div className="space-y-4">
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide">🎯 面试岗位</div>
          {roles.map(role => {
            const isActive = role.id === selectedRole;
            const badge = ROLE_BADGES[role.id] || 'bg-white/10 text-text-main border-white/20';
            return (
              <button
                key={role.id}
                onClick={() => onSelectRole(role.id)}
                className={`w-full text-left rounded-xl border p-4 transition-all ${
                  isActive
                    ? 'border-cyan-400/60 bg-cyan-500/10'
                    : 'border-white/10 bg-white/5 hover:border-white/20'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`}>
                      {role.label}
                    </span>
                    {role.readiness?.ready ? (
                      <CheckCircle2 className="size-4 text-emerald-400" />
                    ) : (
                      <AlertTriangle className="size-4 text-amber-300" />
                    )}
                  </div>
                  <div className="text-[10px] text-text-dim uppercase tracking-wide">
                    {role.requiresThinking ? '需要思考' : '可选思考'}
                  </div>
                </div>
                <div className="mt-2 text-xs text-text-dim">{role.description}</div>
                <div className="mt-3 text-[11px] text-text-main">
                  候选人：{role.candidate?.providerName || '未指派'} {role.candidate?.model ? `• ${role.candidate.model}` : ''}
                </div>
              </button>
            );
          })}
        </div>

        <div className="space-y-4">
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide">👥 应聘者列表</div>
          <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
            {candidates.length === 0 ? (
              <div className="text-xs text-text-dim">暂无已配置模型。</div>
            ) : (
              candidates.map(candidate => (
                <div key={candidate.id} className="flex items-center justify-between text-xs">
                  <div>
                    <div className="text-text-main font-semibold">{candidate.providerName}</div>
                    <div className="text-text-dim">{candidate.model}</div>
                  </div>
                  <div className="text-[10px] text-text-dim text-right">
                    <div>{candidate.roleLabel}</div>
                    <div>
                      思考 {candidate.thinkingSupported ? '通过' : '—'}{' '}
                      {candidate.thinkingConfidence !== null && candidate.thinkingConfidence !== undefined
                        ? `${Math.round(candidate.thinkingConfidence * 100)}%`
                        : ''}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="rounded-xl border border-white/10 bg-black/30 p-4 space-y-3">
            <div className="text-xs font-semibold text-text-main uppercase tracking-wide">🚀 开始面试</div>
            <div className="text-xs text-text-dim">
              {activeRole?.requiresThinking
                ? `核心岗位要求思考型模型（最低 ${Math.round(activeRole.minConfidence * 100)}% 置信度）。`
                : '辅助岗位可使用高效模型，思考能力为加分项。'}
            </div>
            <div className="text-[11px] text-text-dim">
              思考检测：{activeRole?.thinkingConfidence !== null && activeRole?.thinkingConfidence !== undefined
                ? `${Math.round(activeRole.thinkingConfidence * 100)}%`
                : '未检测'}
            </div>
            {disabledReason ? (
              <div className="text-[11px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2">
                {disabledReason}
              </div>
            ) : null}
            <div className="flex items-center gap-2">
              <button
                onClick={onStartInterview}
                disabled={!!disabledReason || running}
                className="px-3 py-2 text-[11px] font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors disabled:opacity-60 flex items-center gap-1"
              >
                <PlayCircle className="size-3" />
                {running ? '面试进行中...' : '开始面试'}
              </button>
              {onRunReadiness ? (
                <button
                  onClick={onRunReadiness}
                  className="px-3 py-2 text-[11px] border border-white/10 rounded hover:border-cyan-400/40"
                >
                  快速筛检
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function InterviewHallV2({
  roles,
  providers,
  selectedRole,
  selectedProvider,
  onSelectRole,
  onSelectProvider,
  onRunConnectivityTest,
  onRunInterview,
  connectivityResults,
  interviewRunning,
  connectivityRunning,
  onSkipConnectivityTest
}: InterviewHallV2Props) {
  const [inspectingProvider, setInspectingProvider] = useState<InterviewProviderSummary | null>(null);
  const activeRole = roles.find(role => role.id === selectedRole);
  const activeProvider = providers.find(provider => provider.id === selectedProvider);
  const activeProviderModel = activeProvider?.model?.trim() || '';
  const connectivityKey = activeRole && selectedProvider ? `${activeRole.id}::${selectedProvider}` : null;
  const directConnectivity = connectivityKey ? connectivityResults.get(connectivityKey) : undefined;
  const desiredModel = activeProviderModel;
  const matchesModel = (value?: ConnectivityResult) => {
    if (!desiredModel) return false;
    if (!value || !value.model) return false;
    return value.model === desiredModel;
  };
  const directMatch = matchesModel(directConnectivity);
  let fallbackConnectivity: ConnectivityResult | undefined;
  if (!directMatch && selectedProvider && desiredModel) {
    let latest = 0;
    connectivityResults.forEach((value, key) => {
      if (!key.endsWith(`::${selectedProvider}`)) return;
      if (!matchesModel(value)) return;
      const time = Date.parse(value.timestamp);
      const parsed = Number.isNaN(time) ? 0 : time;
      if (parsed >= latest) {
        latest = parsed;
        fallbackConnectivity = value;
      }
    });
  }
  const connectivity = directMatch ? directConnectivity : fallbackConnectivity;
  const connectivityNote = connectivity?.sourceRole && connectivity?.sourceRole !== activeRole?.id
    ? `（复用自 ${connectivity.sourceRole}）`
    : !directConnectivity && fallbackConnectivity
      ? '（来自其他岗位）'
      : null;
  const connectivityState =
    connectivity?.ok === true ? 'passed' : connectivity?.ok === false ? 'failed' : 'unknown';
  const connectivityLabel =
    connectivityState === 'passed' ? '连通正常' : connectivityState === 'failed' ? '连通失败' : '连通未测';
  const connectivityColor =
    connectivityState === 'passed'
      ? 'text-emerald-300'
      : connectivityState === 'failed'
        ? 'text-amber-300'
        : 'text-text-dim';
  const connectivityOk = connectivityState === 'passed';
  const canRunConnectivity = Boolean(activeRole && activeProvider && activeProviderModel);
  const canRunInterview = Boolean(activeRole && activeProvider && activeProviderModel);
  const disabledReason = !activeRole
    ? '请选择岗位'
    : !activeProvider
      ? '请选择 LLM 卡片'
      : !activeProviderModel
        ? '当前提供商未配置模型'
        : null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-xs text-text-dim uppercase tracking-wide">LLM 面试中枢</div>
          <h3 className="text-lg font-semibold text-text-main">面试大厅</h3>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-text-dim">
          <ShieldCheck className="size-4 text-emerald-300" />
          核心岗位须配思考型模型。
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_1.3fr_1fr] gap-6">
        <div className="space-y-4">
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide">🎯 面试岗位</div>
          {roles.map(role => {
            const isActive = role.id === selectedRole;
            const badge = ROLE_BADGES[role.id] || 'bg-white/10 text-text-main border-white/20';
            return (
              <button
                key={role.id}
                onClick={() => onSelectRole(role.id)}
                className={`w-full text-left rounded-xl border p-4 transition-all ${
                  isActive
                    ? 'border-cyan-400/60 bg-cyan-500/10'
                    : 'border-white/10 bg-white/5 hover:border-white/20'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-1 text-[10px] uppercase font-semibold rounded border ${badge}`}>
                      {role.label}
                    </span>
                    {role.readiness?.ready ? (
                      <CheckCircle2 className="size-4 text-emerald-400" />
                    ) : (
                      <AlertTriangle className="size-4 text-amber-300" />
                    )}
                  </div>
                  <div className="text-[10px] text-text-dim uppercase tracking-wide">
                    {role.requiresThinking ? '需要思考' : '可选思考'}
                  </div>
                </div>
                <div className="mt-2 text-xs text-text-dim">{role.description}</div>
                <div className="mt-3 text-[11px] text-text-main">
                  默认人选: {role.candidate?.providerName || '未指定'} {role.candidate?.model ? `• ${role.candidate.model}` : ''}
                </div>
              </button>
            );
          })}
        </div>

        <div className="space-y-4">
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide">🤖 LLM 卡片</div>
          {providers.length === 0 ? (
            <div className="rounded-xl border border-white/10 bg-white/5 p-6 text-center text-xs text-text-dim">
              暂无可用 LLM 提供商，请先在配置页添加。
            </div>
          ) : (
            <div className="space-y-3">
              {providers.map(provider => {
                const isActive = provider.id === selectedProvider;
                const styles = STATUS_STYLES[provider.status] || STATUS_STYLES.untested;
                return (
                  <button
                    key={provider.id}
                    onClick={() => onSelectProvider(provider.id)}
                    className={`w-full text-left rounded-xl border p-4 transition-all ${
                      isActive
                        ? 'border-emerald-400/50 bg-emerald-500/10'
                        : `${styles.border} ${styles.bg} hover:border-white/20`
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-text-main">{provider.name}</span>
                          <span className={`text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border ${styles.border} ${styles.text}`}>
                            {STATUS_LABELS[provider.status]}
                          </span>
                          <div className="flex items-center gap-2">
                            <MultiRoleInterviewStatus provider={provider} compact />
                            {(provider.interviewResults && Object.keys(provider.interviewResults).length > 0) && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setInspectingProvider(provider);
                                }}
                                className="text-text-dim hover:text-cyan-200 transition-colors"
                              >
                                <Info className="size-3" />
                              </button>
                            )}
                          </div>
                        </div>
                        <div className="mt-1 text-[10px] text-text-dim">
                          {provider.providerType} • {provider.model || '未设置模型'}
                        </div>
                      </div>
                      <div className="flex flex-col items-end text-[10px] text-text-dim">
                        <span className={`flex items-center gap-1 ${styles.text}`}>
                          <span className={`size-2 rounded-full ${styles.dot}`} />
                          {provider.status === 'testing' ? '连通测试中' : '连通状态'}
                        </span>
                        <span className="mt-1">{formatTimestamp(provider.lastConnectivityTest?.timestamp)}</span>
                      </div>
                    </div>
                    {provider.lastConnectivityTest ? (
                      <div className="mt-3 text-[10px] text-text-dim">
                        延迟 {provider.lastConnectivityTest.latencyMs ? `${Math.round(provider.lastConnectivityTest.latencyMs)}ms` : '—'}
                        {provider.lastConnectivityTest.error ? ` • ${provider.lastConnectivityTest.error}` : ''}
                      </div>
                    ) : null}
                    {provider.thinkingConfidence !== undefined && provider.thinkingConfidence !== null ? (
                      <div className="mt-2 text-[10px] text-text-dim">
                        思考置信度：{Math.round(provider.thinkingConfidence * 100)}%
                        {provider.thinkingSupported === false ? ' (不支持)' : ''}
                      </div>
                    ) : null}
                    {provider.lastInterview ? (
                      <div className="mt-2 text-[10px] text-text-dim">
                        最近面试：{provider.interviewStatus === 'passed' ? '通过' : '未通过'} • {formatTimestamp(provider.lastInterview.timestamp)}
                      </div>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="space-y-4">
          <div className="text-xs font-semibold text-text-main uppercase tracking-wide">🧪 测试控制区</div>
          <div className="rounded-xl border border-white/10 bg-black/30 p-4 space-y-4">
            <div className="space-y-2">
              <div className="text-xs text-text-main font-semibold">当前组合</div>
              <div className="text-[11px] text-text-dim">
                岗位：{activeRole?.label || '未选择'}
              </div>
              <div className="text-[11px] text-text-dim">
                模型：{activeProvider?.name || '未选择'} {activeProviderModel ? `• ${activeProviderModel}` : ''}
              </div>
            </div>

            <div className="rounded-lg border border-white/10 bg-white/5 p-3 text-[11px] text-text-dim">
              {activeRole?.requiresThinking
                ? `核心岗位要求思考型模型（最低 ${Math.round(activeRole.minConfidence * 100)}%）。`
                : '辅助岗位可使用高效模型，思考能力为加分项。'}
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-text-dim">连通性测试</span>
                {connectivityRunning ? (
                  <span className="flex items-center gap-1 text-cyan-200"><Loader2 className="size-3 animate-spin" />运行中</span>
                ) : (
                  <span className={connectivityColor}>
                    {connectivityLabel}
                  </span>
                )}
              </div>
              <div className="text-[10px] text-text-dim">
                {connectivity?.timestamp
                  ? `最近：${formatTimestamp(connectivity.timestamp)}${connectivityNote ? ` ${connectivityNote}` : ''}`
                  : '尚无记录'}
              </div>
              {connectivity?.error ? (
                <div className="text-[10px] text-red-300">{connectivity.error}</div>
              ) : null}
              {!connectivityOk && onSkipConnectivityTest && activeRole && activeProvider ? (
                <button
                  type="button"
                  onClick={() => onSkipConnectivityTest(activeRole.id, activeProvider.id)}
                  className="px-2 py-1 text-[10px] border border-amber-500/40 text-amber-300 rounded hover:bg-amber-500/10 transition-colors"
                >
                  跳过连通性测试
                </button>
              ) : null}
            </div>

            {disabledReason ? (
              <div className="text-[11px] text-red-200 bg-red-500/10 border border-red-500/20 rounded p-2">
                {disabledReason}
              </div>
            ) : null}

            <div className="flex flex-col gap-2">
              <button
                onClick={() => {
                  if (activeRole && activeProvider && activeProviderModel) {
                    onRunConnectivityTest({ 
                      role: activeRole.id, 
                      providerId: activeProvider.id, 
                      model: activeProviderModel
                    });
                  }
                }}
                disabled={!canRunConnectivity || connectivityRunning}
                className="px-3 py-2 text-[11px] font-semibold bg-cyan-500/80 hover:bg-cyan-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1"
              >
                <Cpu className="size-3" />
                {connectivityRunning ? '连通性测试中...' : '连通性测试'}
              </button>
              <button
                onClick={() => {
                  if (activeRole && activeProvider && activeProviderModel) {
                    onRunInterview({ 
                      role: activeRole.id, 
                      providerId: activeProvider.id, 
                      model: activeProviderModel
                    });
                  }
                }}
                disabled={!canRunInterview || interviewRunning}
                className="px-3 py-2 text-[11px] font-semibold bg-emerald-500/80 hover:bg-emerald-500 text-white rounded transition-colors disabled:opacity-60 flex items-center justify-center gap-1"
              >
                <Zap className="size-3" />
                {interviewRunning ? '面试进行中...' : '深度面试'}
              </button>
            </div>
          </div>
        </div>
      </div>
      {inspectingProvider && (
        <InterviewDetailsModal provider={inspectingProvider} onClose={() => setInspectingProvider(null)} />
      )}
    </div>
  );
}

export function InterviewHall(props: InterviewHallProps) {
  if ('providers' in props) {
    return <InterviewHallV2 {...props} />;
  }
  return <InterviewHallLegacy {...props} />;
}
