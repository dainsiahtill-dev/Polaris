import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/app/components/ui/alert-dialog';
import type { AgentsReviewInfo } from '@/app/types/appContracts';
import { workspaceFileLabel } from '@/app/utils/workspaceDisplay';

interface AgentsReviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentsDraftFailed: boolean;
  agentsReview: AgentsReviewInfo | null;
  onOpenLogs: () => void;
  onOpenDraft: () => void;
  workspace?: string;
  agentsDraftMtime: string;
  agentsFeedbackSavedAt: string;
  agentsLoading: boolean;
  agentsDraftContent: string;
  agentsFeedback: string;
  onAgentsFeedbackChange: (value: string) => void;
  onRetryGenerate: () => void;
  onSubmitFeedback: () => void;
  onApplyDraft: () => void;
  agentsApplying: boolean;
}

export function AgentsReviewDialog({
  open,
  onOpenChange,
  agentsDraftFailed,
  agentsReview,
  onOpenLogs,
  onOpenDraft,
  workspace,
  agentsDraftMtime,
  agentsFeedbackSavedAt,
  agentsLoading,
  agentsDraftContent,
  agentsFeedback,
  onAgentsFeedbackChange,
  onRetryGenerate,
  onSubmitFeedback,
  onApplyDraft,
  agentsApplying,
}: AgentsReviewDialogProps) {
  const targetPath = workspace ? `${workspace}\\AGENTS.md` : 'workspace\\AGENTS.md';
  const targetLabel = workspaceFileLabel(workspace);

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent
        data-testid="agents-review-dialog"
        className="grid max-h-[92vh] min-w-0 max-w-3xl grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden border border-emerald-500/30 bg-[#1f2125]"
      >
        <AlertDialogHeader className="shrink-0">
          <AlertDialogTitle className="text-emerald-200">
            {agentsDraftFailed ? 'AGENTS.md 草案生成失败' : 'AGENTS.md 草案待审'}
          </AlertDialogTitle>
          <AlertDialogDescription className="whitespace-pre-wrap text-gray-300">
            {agentsDraftFailed
              ? '未能生成可用草案。请先查看日志，补充修订意见后再重试。'
              : '请审阅生成的 AGENTS.md 草案；确认后可回写到工作区，供 PM/Director 后续轮次遵循。'}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="min-w-0 shrink-0 rounded-md border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-100">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <span className="min-w-0 break-all">
              草案路径: {agentsReview?.draft_path || 'runtime/contracts/agents.generated.md'}
            </span>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={onOpenLogs}
                className="rounded bg-emerald-500/20 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-500/30"
              >
                查看日志
              </button>
              <button
                type="button"
                onClick={onOpenDraft}
                className="rounded bg-emerald-500/20 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-500/30"
              >
                打开草案
              </button>
            </div>
          </div>
          <div className="truncate" title={targetPath}>
            目标位置: <span data-testid="agents-review-target-label">{targetLabel}</span>
          </div>
          {agentsDraftMtime ? <div>生成时间: {agentsDraftMtime}</div> : null}
          {agentsFeedbackSavedAt ? <div>反馈保存时间: {agentsFeedbackSavedAt}</div> : null}
        </div>
        <div data-testid="agents-review-scroll-region" className="grid min-h-0 gap-3 overflow-y-auto pr-1">
          <div className="min-h-0 rounded-md border border-gray-700 bg-[#181a1f]">
            <div className="flex min-w-0 items-center justify-between gap-2 border-b border-gray-800 px-3 py-2 text-xs text-gray-400">
              <span className="min-w-0 truncate">AGENTS.generated.md</span>
              {agentsLoading ? <span>加载中...</span> : null}
            </div>
            <pre className="max-h-[38vh] min-h-48 overflow-auto whitespace-pre-wrap break-words p-3 text-xs text-gray-200">
              {agentsDraftContent || '(空)'}
            </pre>
          </div>
          <div className="rounded-md border border-gray-700 bg-[#181a1f] p-3">
            <div className="text-xs text-gray-400">修订意见（将反馈给 PM）</div>
            <textarea
              className="mt-2 h-28 w-full resize-none rounded border border-gray-700 bg-[#0f1115] p-2 text-xs text-gray-200 outline-none focus:border-emerald-400"
              placeholder="请说明需要修改的规则、术语或格式要求（UTF-8）..."
              value={agentsFeedback}
              onChange={(event) => {
                onAgentsFeedbackChange(event.target.value);
              }}
            />
            <div className="mt-2 text-[11px] text-gray-500">
              提交反馈不会自动回写；需确认草案后再执行应用。
            </div>
          </div>
        </div>
        <AlertDialogFooter data-testid="agents-review-footer" className="shrink-0 flex-wrap">
          <AlertDialogCancel className="whitespace-nowrap" onClick={() => onOpenChange(false)}>取消</AlertDialogCancel>
          {agentsDraftFailed ? (
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                onRetryGenerate();
              }}
              className="whitespace-nowrap bg-blue-500 text-white hover:bg-blue-400"
            >
              重新生成
            </AlertDialogAction>
          ) : null}
          <AlertDialogAction
            onClick={(event) => {
              event.preventDefault();
              onSubmitFeedback();
            }}
            className="whitespace-nowrap bg-blue-500 text-white hover:bg-blue-400"
          >
            提交反馈（暂不回写）
          </AlertDialogAction>
          <AlertDialogAction
            onClick={onApplyDraft}
            disabled={agentsApplying || agentsDraftFailed}
            className="whitespace-nowrap bg-emerald-500 text-white hover:bg-emerald-400"
          >
            {agentsApplying ? '回写中...' : '回写到 AGENTS.md'}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

