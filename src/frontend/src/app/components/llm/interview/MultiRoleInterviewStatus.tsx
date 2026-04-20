import React from 'react';
import type { InterviewProviderSummary, InterviewResultDetail } from './InterviewHall';

type RoleId = 'pm' | 'director' | 'chief_engineer' | 'qa' | 'architect' | 'cfo' | 'hr';

const ROLE_META: Record<RoleId, { label: string; badge: string }> = {
  pm: { label: '尚书令', badge: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30' },
  director: { label: '工部侍郎', badge: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30' },
  chief_engineer: { label: '工部尚书', badge: 'bg-emerald-600/20 text-emerald-300 border-emerald-600/30' },
  qa: { label: '门下侍中', badge: 'bg-blue-500/20 text-blue-200 border-blue-500/30' },
  architect: { label: '中书令', badge: 'bg-amber-500/20 text-amber-200 border-amber-500/30' },
  cfo: { label: '户部尚书', badge: 'bg-purple-500/20 text-purple-200 border-purple-500/30' },
  hr: { label: '吏部尚书', badge: 'bg-pink-500/20 text-pink-200 border-pink-500/30' }
};

export const RoleBadge: React.FC<{ roleId: RoleId; result: InterviewResultDetail }> = ({ roleId, result }) => {
  if (result.status === 'none') return null;
  const isSuccess = result.status === 'passed';
  const meta = ROLE_META[roleId];
  return (
    <div className={`inline-flex items-center gap-1 text-[8px] px-1.5 py-0.5 rounded border ${isSuccess ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-rose-500/40 bg-rose-500/10 text-rose-300'}`}>
      <span className={`px-1 py-0.5 rounded ${meta.badge}`}>{meta.label.split(' ')[0]}</span>
      <span>{isSuccess ? '✓' : '✗'}</span>
    </div>
  );
};

export const MultiRoleInterviewStatus: React.FC<{ provider: InterviewProviderSummary; compact?: boolean }> = ({ provider, compact = false }) => {
  const results = provider.interviewResults || {};
  const resultValues = Object.values(results) as InterviewResultDetail[];
  const resultEntries = Object.entries(results) as [string, InterviewResultDetail][];
  const passedCount = resultValues.filter((r: InterviewResultDetail) => r.status === 'passed').length;
  const failedCount = resultValues.filter((r: InterviewResultDetail) => r.status === 'failed').length;
  
  if (passedCount === 0 && failedCount === 0) {
    if (provider.interviewStatus && provider.interviewStatus !== 'none') {
      const isSuccess = provider.interviewStatus === 'passed';
      return <span className={`text-[9px] uppercase px-1.5 py-0.5 rounded border ${isSuccess ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300' : 'border-rose-500/40 bg-rose-500/10 text-rose-300'}`}>{isSuccess ? '面试通过' : '面试失败'}</span>;
    }
    return null;
  }
  
  if (compact) {
    return (
      <div className="flex items-center gap-1">
        {passedCount > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-[8px] text-emerald-300 bg-emerald-500/10 px-1.5 py-0.5 rounded">+{passedCount}</span>
            <div className="flex -space-x-1">
              {resultEntries.filter(([_, r]) => r.status === 'passed').map(([roleId]) => (
                <div key={roleId} className={`w-3 h-3 rounded-full border border-white/20 ${ROLE_META[roleId as RoleId].badge}`} title={ROLE_META[roleId as RoleId].label} />
              ))}
            </div>
          </div>
        )}
        {failedCount > 0 && (
          <div className="flex items-center gap-1">
            <span className="text-[8px] text-rose-300 bg-rose-500/10 px-1.5 py-0.5 rounded">-{failedCount}</span>
            <div className="flex -space-x-1 opacity-50">
              {resultEntries.filter(([_, r]) => r.status === 'failed').map(([roleId]) => (
                <div key={roleId} className={`w-3 h-3 rounded-full border border-white/20 ${ROLE_META[roleId as RoleId].badge}`} title={ROLE_META[roleId as RoleId].label} />
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }
  
  return (
    <div className="flex flex-wrap gap-1">
      {resultEntries.map(([roleId, result]) => (
        <RoleBadge key={roleId} roleId={roleId as RoleId} result={result} />
      ))}
    </div>
  );
};

export const InterviewDetailsModal: React.FC<{ provider: InterviewProviderSummary; onClose: () => void }> = ({ provider, onClose }) => {
  const results = provider.interviewResults || {};
  const resultValues = Object.values(results) as InterviewResultDetail[];
  const resultEntries = Object.entries(results) as [string, InterviewResultDetail][];
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-black/90 border border-white/20 rounded-xl p-4 max-w-md w-full max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-text-main">{provider.name} 面试详情</h3>
          <button onClick={onClose} className="text-text-dim hover:text-text-main">✕</button>
        </div>
        <div className="space-y-4">
          <div className="p-3 bg-white/5 rounded-lg">
            <div className="text-[10px] text-text-dim mb-1">模型信息</div>
            <div className="text-xs text-text-main">{provider.model}</div>
            <div className="text-[9px] text-text-dim">{provider.providerType}</div>
          </div>
          <div>
            <div className="text-[10px] text-text-dim mb-2">面试结果</div>
            <div className="space-y-2">
              {resultEntries.map(([roleId, result]) => {
                if (result.status === 'none') return null;
                const meta = ROLE_META[roleId as RoleId];
                const isSuccess = result.status === 'passed';
                return (
                  <div key={roleId} className="flex items-center justify-between p-2 border border-white/10 rounded">
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-1 text-[9px] rounded ${meta.badge}`}>{meta.label}</span>
                      <span className={`text-[10px] ${isSuccess ? 'text-emerald-300' : 'text-rose-300'}`}>{isSuccess ? '通过' : '失败'}</span>
                    </div>
                    <div className="flex items-center gap-2 text-[9px] text-text-dim">
                      {result.score && <span className="bg-white/10 px-1.5 py-0.5 rounded">分数: {result.score.toFixed(1)}</span>}
                      {result.timestamp && <span>{new Date(result.timestamp).toLocaleDateString()}</span>}
                    </div>
                  </div>
                );
              })}
              {resultValues.filter((r: InterviewResultDetail) => r.status !== 'none').length === 0 && (
                <div className="text-[10px] text-text-dim text-center py-4">暂无面试记录</div>
              )}
            </div>
          </div>
        </div>
        <button onClick={onClose} className="mt-4 w-full px-3 py-1.5 text-[10px] bg-white/10 hover:bg-white/20 rounded">关闭</button>
      </div>
    </div>
  );
};
