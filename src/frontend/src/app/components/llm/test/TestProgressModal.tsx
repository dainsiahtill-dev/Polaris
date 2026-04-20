import { X, Loader2, RotateCw, Clipboard } from 'lucide-react';
import { TestLogViewer } from './TestLogViewer';
import { TestProgressBar } from './TestProgressBar';
import { TestResultDisplay } from './TestResultDisplay';
import type { TestState, TestStep } from './types';

interface TestProgressModalProps {
  open: boolean;
  state: TestState;
  steps?: TestStep[];
  onClose: () => void;
  onCancel?: () => void;
  onRetry?: () => void;
  onCopyReport?: () => void;
}

const STATUS_LABELS: Record<TestState['status'], string> = {
  idle: '等待中',
  running: '测试中',
  success: '通过',
  failed: '失败',
  cancelled: '已取消'
};

const STATUS_BADGES: Record<TestState['status'], string> = {
  idle: 'bg-gray-500/20 text-gray-300 border-gray-500/30',
  running: 'bg-cyan-500/20 text-cyan-200 border-cyan-500/30',
  success: 'bg-emerald-500/20 text-emerald-200 border-emerald-500/30',
  failed: 'bg-red-500/20 text-red-200 border-red-500/30',
  cancelled: 'bg-amber-500/20 text-amber-200 border-amber-500/30'
};

export function TestProgressModal({
  open,
  state,
  steps,
  onClose,
  onCancel,
  onRetry,
  onCopyReport
}: TestProgressModalProps) {
  if (!open) return null;
  const targetName = state.target?.providerName || 'LLM Provider';
  const modelName = state.target?.model ? ` • ${state.target.model}` : '';
  const running = state.status === 'running';

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[70] p-4">
      <div className="bg-bg-panel/95 border border-white/10 rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl shadow-purple-900/20">
        <div className="flex items-center justify-between p-4 border-b border-white/10">
          <div>
            <div className="text-sm font-semibold text-text-main">测试进度 - {targetName}{modelName}</div>
            <div className="text-[10px] text-text-dim mt-1">
              {state.currentStep || '准备中'}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-[10px] uppercase tracking-wider px-2 py-1 rounded border ${STATUS_BADGES[state.status]}`}>
              {STATUS_LABELS[state.status]}
            </span>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded border border-white/10 hover:border-accent/40 transition-colors"
              disabled={running}
            >
              {running ? <Loader2 className="size-3 animate-spin" /> : <X className="size-3" />}
            </button>
          </div>
        </div>

        <div className="p-4 space-y-4 overflow-auto">
          <TestProgressBar progress={state.progress} running={running} />

          {steps && steps.length > 0 ? (
            <div className="grid grid-cols-5 gap-2 text-[9px] text-text-dim">
              {steps.map((step) => (
                <div
                  key={step.key}
                  className={`px-2 py-1 rounded border text-center ${
                    state.currentStep === step.label
                      ? 'border-cyan-500/50 text-cyan-200 bg-cyan-500/10'
                      : 'border-white/10'
                  }`}
                >
                  {step.label}
                </div>
              ))}
            </div>
          ) : null}

          {state.error ? (
            <div className="text-xs text-red-200 bg-red-500/10 border border-red-500/30 rounded p-2">
              {state.error}
            </div>
          ) : null}

          <TestLogViewer logs={state.logs} />

          {state.result ? <TestResultDisplay result={state.result} /> : null}
        </div>

        <div className="flex items-center justify-between p-4 border-t border-white/10 bg-black/30">
          <div className="text-[10px] text-text-dim">
            {state.startedAt ? `开始时间 ${new Date(state.startedAt).toLocaleTimeString()}` : ''}
            {state.finishedAt ? ` · 完成时间 ${new Date(state.finishedAt).toLocaleTimeString()}` : ''}
          </div>
          <div className="flex items-center gap-2">
            {state.result?.report && onCopyReport ? (
              <button
                type="button"
                onClick={onCopyReport}
                className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40 flex items-center gap-1"
              >
                <Clipboard className="size-3" />
                复制报告
              </button>
            ) : null}
            {state.status === 'failed' && onRetry ? (
              <button
                type="button"
                onClick={onRetry}
                className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-emerald-400/40 flex items-center gap-1"
              >
                <RotateCw className="size-3" />
                重试
              </button>
            ) : null}
            {running ? (
              <button
                type="button"
                onClick={onCancel}
                className="px-3 py-1.5 text-[10px] border border-red-500/30 text-red-200 rounded hover:border-red-500/60"
              >
                取消测试
              </button>
            ) : (
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 text-[10px] border border-white/10 rounded hover:border-accent/40"
              >
                关闭
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
