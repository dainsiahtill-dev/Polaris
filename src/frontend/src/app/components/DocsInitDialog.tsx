import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RiAiGenerate2 } from 'react-icons/ri';
import { Landmark, ScrollText, Stamp, Brain, ChevronLeft, Loader2, X, FileText, CheckCircle2 } from 'lucide-react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';
import { useNDJSONStream, type NDJSONEvent } from '@/hooks/useNDJSONStream';
import {
  normalizeDocsInitPreviewPayload,
  type DocsInitPreview,
} from '@/app/utils/docsInitPreview';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { ScrollArea } from '@/app/components/ui/scroll-area';

const WIZARD_MODE = 'minimal';
const INVALID_PREVIEW_ERROR = 'Plan preview data incomplete, please redraft plan.';
const SLOT_LABELS: Record<string, string> = {
  deployment_mode: '部署方式',
  auth_mode: '访问控制',
  file_size_limit: '文件规模',
  file_ops_scope: '目录与删除',
  load_test_requirement: '并发压测',
};

export interface WorkspaceStatus {
  status: string;
  reason?: string;
  actions?: string[];
  workspace_path?: string;
  timestamp?: string;
}

interface DocsInitDialogueTurn {
  role: 'user' | 'assistant';
  content: string;
  reasoning?: string;
  questions?: string[];
  /** Raw accumulated streaming content (may be JSON); kept separate from display content. */
  rawContent?: string;
}

interface DocsInitDialogueResponse {
  reply?: string;
  questions?: string[];
  tiaochen?: string[];
  fields?: Record<string, string>;
  meta?: {
    phase?: string;
    answered_slots?: string[];
    unresolved_slots?: string[];
  };
  handoffs?: {
    pm?: string[];
    director?: string[];
  };
}

interface DocsInitDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspace?: string;
  workspaceStatus?: WorkspaceStatus | null;
  docsPresent?: boolean;
  onApplied?: () => void;
}

function splitLines(value: string): string[] {
  return String(value || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function buildStreamingThoughtPreview(raw: string): string {
  const text = String(raw || '').trim();
  if (!text) return '';
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    const reply = String(parsed.reply || '').trim();
    if (reply) return reply;
  } catch {
    // continue with raw preview
  }
  const compact = text.replace(/\s+/g, ' ').trim();
  if (!compact) return '';
  return compact.slice(-360);
}

export function DocsInitDialog({
  open,
  onOpenChange,
  workspace,
  workspaceStatus,
  docsPresent,
  onApplied,
}: DocsInitDialogProps) {
  const [step, setStep] = useState(2);
  const [goal, setGoal] = useState('');
  const [inScope, setInScope] = useState('');
  const [outOfScope, setOutOfScope] = useState('');
  const [constraints, setConstraints] = useState('');
  const [definitionOfDone, setDefinitionOfDone] = useState('');
  const [backlog, setBacklog] = useState('');
  const [tingyiMessage, setTingyiMessage] = useState('');
  const [dialogueTurns, setDialogueTurns] = useState<DocsInitDialogueTurn[]>([]);
  const [dialogueMeta, setDialogueMeta] = useState<{ phase: string; answered_slots: string[]; unresolved_slots: string[] }>({
    phase: 'clarifying',
    answered_slots: [],
    unresolved_slots: [],
  });
  const [tiaochenDraft, setTiaochenDraft] = useState<string[]>([]);
  const [preview, setPreview] = useState<DocsInitPreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [applying, setApplying] = useState(false);
  const [dialoguing, setDialoguing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Draft Plan streaming progress state
  const [previewProgress, setPreviewProgress] = useState<{
    open: boolean;
    stage: string;
    message: string;
    progress: number;
    thinking?: string;
    fields?: Record<string, string[]>;
  }>({ open: false, stage: '', message: '', progress: 0 });

  const docsMissing = useMemo(() => {
    if (docsPresent === false) return true;
    return workspaceStatus?.status === 'NEEDS_DOCS_INIT';
  }, [docsPresent, workspaceStatus?.status]);
  const displayWorkspace = workspaceLabel(workspace, '');

  const applySuggestedFields = (fields: Record<string, string> | undefined) => {
    if (!fields) return;
    if (typeof fields.goal === 'string') setGoal(fields.goal);
    if (typeof fields.in_scope === 'string') setInScope(fields.in_scope);
    if (typeof fields.out_of_scope === 'string') setOutOfScope(fields.out_of_scope);
    if (typeof fields.constraints === 'string') setConstraints(fields.constraints);
    if (typeof fields.definition_of_done === 'string') setDefinitionOfDone(fields.definition_of_done);
    if (typeof fields.backlog === 'string') setBacklog(fields.backlog);
  };

  useEffect(() => {
    if (!open) return;
    setStep(2);
    setGoal('');
    setInScope('');
    setOutOfScope('');
    setConstraints('');
    setDefinitionOfDone('');
    setBacklog('');
    setTingyiMessage('');
    setDialogueTurns([]);
    setDialogueMeta({ phase: 'clarifying', answered_slots: [], unresolved_slots: [] });
    setTiaochenDraft([]);
    setPreview(null);
    setError(null);
    setLoadingPreview(false);
    setApplying(false);
    setDialoguing(false);
  }, [open, workspace]);

  const streamingIndexRef = useRef<number>(-1);

  const onRawEvent = useCallback((event: NDJSONEvent) => {
    if (event.type === 'reasoning_chunk') {
      const content = String(event.data.content || '');
      setDialogueTurns((prev) => {
        const idx = streamingIndexRef.current;
        if (idx < 0 || idx >= prev.length) return prev;
        const copy = [...prev];
        copy[idx] = { ...copy[idx], reasoning: (copy[idx].reasoning || '') + content };
        return copy;
      });
    }
    if (event.type === 'thinking_chunk') {
      const content = String(event.data.content || '');
      if (!content) return;
      setDialogueTurns((prev) => {
        const idx = streamingIndexRef.current;
        if (idx < 0 || idx >= prev.length) return prev;
        const copy = [...prev];
        copy[idx] = { ...copy[idx], rawContent: (copy[idx].rawContent || '') + content };
        return copy;
      });
    }
  }, []);

  // Preview 流式进度事件处理
  const onPreviewRawEvent = useCallback((event: NDJSONEvent) => {
    if (event.type === 'stage') {
      const data = event.data as { stage: string; message: string; progress: number; fields?: Record<string, string[]> };
      setPreviewProgress((prev) => ({
        ...prev,
        open: true,
        stage: data.stage,
        message: data.message,
        progress: data.progress,
        fields: data.fields || prev.fields,
      }));
    } else if (event.type === 'thinking') {
      // 实时更新thinking内容
      const data = event.data as { content: string; accumulated?: string };
      setPreviewProgress((prev) => ({
        ...prev,
        thinking: data.accumulated || data.content,
      }));
    }
  }, []);

  const onPreviewComplete = useCallback((data: Record<string, unknown>) => {
    const previewData = normalizeDocsInitPreviewPayload(data);
    if (!previewData) {
      setPreview(null);
      setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
      setLoadingPreview(false);
      setError(INVALID_PREVIEW_ERROR);
      return;
    }
    setPreview(previewData);
    if (tiaochenDraft.length === 0 && backlog.trim()) {
      setTiaochenDraft(splitLines(backlog));
    }
    setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
    setStep(3);
    setLoadingPreview(false);
  }, [backlog, tiaochenDraft]);

  const onPreviewError = useCallback((err: string) => {
    setError(err || '条陈拟稿失败');
    setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
    setLoadingPreview(false);
  }, []);

  const { isStreaming: isPreviewStreaming, startStream: startPreviewStream, stopStream: stopPreviewStream } =
    useNDJSONStream({ onEvent: onPreviewRawEvent, onComplete: onPreviewComplete, onError: onPreviewError });

  const onComplete = useCallback(
    (data: Record<string, unknown>) => {
      const reply = String(data.reply || '').trim();
      const questions = ((data.questions || []) as string[]).map((s) => String(s).trim()).filter(Boolean);
      const assistantContent = reply || (questions.length ? questions.join('\n') : 'Plan updated based on discussion.');

      setDialogueTurns((prev) => {
        const idx = streamingIndexRef.current;
        if (idx < 0 || idx >= prev.length) {
          const lastIdx = prev.length - 1;
          if (lastIdx >= 0 && prev[lastIdx].role === 'assistant') {
            const copy = [...prev];
            copy[lastIdx] = {
              role: 'assistant',
              content: assistantContent,
              questions,
              reasoning: copy[lastIdx].reasoning,
            };
            return copy;
          }
          return prev;
        }
        const copy = [...prev];
        copy[idx] = {
          role: 'assistant',
          content: assistantContent,
          questions,
          reasoning: copy[idx].reasoning,
        };
        return copy;
      });
      streamingIndexRef.current = -1;

      const fields = data.fields as Record<string, string> | undefined;
      applySuggestedFields(fields);
      const meta = (data.meta || {}) as Record<string, unknown>;
      const answeredSlots = Array.isArray(meta.answered_slots)
        ? meta.answered_slots.map((s) => String(s).trim()).filter(Boolean)
        : [];
      const unresolvedSlots = Array.isArray(meta.unresolved_slots)
        ? meta.unresolved_slots.map((s) => String(s).trim()).filter(Boolean)
        : [];
      setDialogueMeta({
        phase: String(meta.phase || (unresolvedSlots.length > 0 ? 'clarifying' : 'ready_for_draft')).trim(),
        answered_slots: answeredSlots,
        unresolved_slots: unresolvedSlots,
      });

      const tiaochen = ((data.tiaochen || []) as string[]).map((item) => String(item).trim()).filter(Boolean);
      if (tiaochen.length > 0) {
        setTiaochenDraft(tiaochen);
      } else if (fields?.backlog) {
        setTiaochenDraft(splitLines(fields.backlog));
      } else if (backlog) {
        setTiaochenDraft(splitLines(backlog));
      }

      setTingyiMessage('');
      setDialoguing(false);
    },
    [backlog],
  );

  const onError = useCallback((error: string) => {
    streamingIndexRef.current = -1;
    setError(error);
    toast.error(error || 'Dialogue failed.');
    setDialoguing(false);
  }, []);

  const { isStreaming, startStream, stopStream } = useNDJSONStream({ onEvent: onRawEvent, onComplete, onError });

  const runDialogue = async () => {
    const message = tingyiMessage.trim() || goal.trim();
    if (!message) {
      toast.error('Please enter discussion content or project goal.');
      return;
    }
    setDialoguing(true);
    setError(null);

    const userTurn: DocsInitDialogueTurn = { role: 'user', content: message };
    const historyPayload = [
      ...dialogueTurns.map((turn) => ({
        role: turn.role,
        content: turn.content,
        questions: turn.role === 'assistant' ? (turn.questions || []) : [],
      })),
      { role: 'user', content: message },
    ];

    setDialogueTurns((prev) => {
      const next = [...prev, userTurn, { role: 'assistant' as const, content: '' }];
      streamingIndexRef.current = next.length - 1;
      return next;
    });

    startStream('/v2/docs/init/dialogue/jetstream', {
      message,
      goal,
      in_scope: inScope,
      out_of_scope: outOfScope,
      constraints,
      definition_of_done: definitionOfDone,
      backlog,
      history: historyPayload,
    });
  };

  const buildPreview = async () => {
    setLoadingPreview(true);
    setError(null);
    setPreviewProgress({ open: true, stage: 'init', message: '初始化文档生成环境...', progress: 5 });

    startPreviewStream('/v2/docs/init/preview/jetstream', {
      mode: WIZARD_MODE,
      goal,
      in_scope: inScope,
      out_of_scope: outOfScope,
      constraints,
      definition_of_done: definitionOfDone,
      backlog,
    });
  };

  const applyDocs = async () => {
    if (!preview || preview.files.length === 0) {
      setError(INVALID_PREVIEW_ERROR);
      return;
    }
    setApplying(true);
    setError(null);
    try {
      const res = await apiFetch('/v2/docs/init/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: preview.mode || WIZARD_MODE,
          target_root: preview.target_root,
          files: preview.files.map((file) => ({
            path: file.path,
            content: file.content,
          })),
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || 'Approve write failed.');
      }
      toast.success('Plan approved');
      onApplied?.();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Approve write failed.');
    } finally {
      setApplying(false);
    }
  };

  const updatePreviewFile = (index: number, content: string) => {
    setPreview((prev) => {
      if (!prev) return prev;
      const nextFiles = [...prev.files];
      nextFiles[index] = { ...nextFiles[index], content };
      return { ...prev, files: nextFiles };
    });
  };

  const phaseReady = dialogueMeta.phase === 'ready_for_draft';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="docs-init-dialog" className="soft-panel grid h-[min(96vh,920px)] w-[98vw] max-w-[98vw] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-xl text-[#F8FAFC]">

        {/* ── Header ── */}
        <DialogHeader className="relative pb-3 border-b border-white/[0.08]">
          <div className="absolute inset-x-0 bottom-0 h-px soft-divider opacity-60" />
          <div className="flex min-w-0 flex-col gap-3 pr-10 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-3">
                <div className="soft-raised flex h-9 w-9 items-center justify-center rounded-lg">
                  <Landmark className="size-4.5 text-[var(--soft-accent)]" />
                </div>
                <div className="min-w-0">
                  <DialogTitle className="break-words text-lg font-bold tracking-wide">
                    <span className="text-[var(--soft-accent)]">政 事 堂</span>
                    <span className="ml-2.5 text-xs font-normal tracking-widest text-white/30">Architect Discussion Planning</span>
                  </DialogTitle>
                  <DialogDescription className="mt-0.5 text-[11px] text-[#F8FAFC]/30">
                    {docsMissing
                      ? 'Current workspace missing docs/, please start Docs Wizard discussion first.'
                      : 'Dialogue Q&A → Draft Plan → Approve ┃ Full process bound to Architect LLM'}
                  </DialogDescription>
                </div>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-3 pt-1">
              <div className="soft-chip flex items-center gap-2 rounded-lg px-3 py-1.5">
                <div className={`h-1.5 w-1.5 rounded-full ${phaseReady ? 'bg-[#22C55E]' : 'bg-[var(--soft-accent)] animate-pulse'}`} />
                <span className="text-[10px] tracking-widest text-white/60">
                  {phaseReady ? 'Discussion ready · Can draft plan' : 'Discussion in progress'}
                </span>
              </div>
              <span className="text-[10px] text-white/25 font-mono">
                {step === 3 ? 'II' : 'I'} / II
              </span>
            </div>
          </div>
          {workspace ? (
            <div
              data-testid="docs-init-workspace-label"
              className="mt-2 truncate pr-10 font-mono text-[10px] tracking-wider text-white/25"
              title={workspace}
            >
              Workspace ▸ {displayWorkspace}
            </div>
          ) : null}
        </DialogHeader>

        <div data-testid="docs-init-body" className="min-h-0 overflow-hidden">
          {error ? (
            <div className="soft-raised mb-3 break-words rounded-lg border-red-500/30 bg-red-500/10 px-4 py-2.5 text-xs text-red-200">
              <span className="mr-2 text-red-400">!</span>{error}
            </div>
          ) : null}

        {/* ── Draft Plan progress modal ── */}
        {previewProgress.open && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="soft-panel w-full max-w-md rounded-xl p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="relative">
                  <div className="h-10 w-10 rounded-full border-2 border-white/10 border-t-[var(--soft-accent)] animate-spin" />
                  <ScrollText className="absolute inset-0 m-auto h-4 w-4 text-[var(--soft-accent)]" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-[var(--soft-accent)]">Drafting Plan...</h3>
                  <p className="text-[11px] text-white/50">Architect is generating documents for you</p>
                </div>
              </div>

              {/* 进度条 */}
              <div className="mb-4">
                <div className="h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="soft-progress h-full rounded-full transition-all duration-500"
                    style={{ width: `${previewProgress.progress}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-white/40">
                  <span>{previewProgress.progress}%</span>
                  <span>{previewProgress.stage === 'llm_start' ? 'AI思考中' : '处理中'}</span>
                </div>
              </div>

              {/* 当前步骤 */}
              <div className="soft-inset mb-4 rounded-lg p-3">
                <div className="flex min-w-0 items-center gap-2 text-xs text-white/70">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--soft-accent)]" />
                  <span className="min-w-0 break-words">{previewProgress.message}</span>
                </div>              </div>

              {/* 实时思考内容 */}
              {previewProgress.thinking ? (
                <div className="soft-inset mb-4 max-h-48 overflow-y-auto rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <Brain className="h-3 w-3 text-[var(--soft-accent)]" />
                    <span className="text-[10px] font-bold text-[var(--soft-accent)]/60">Architect thinking...</span>
                  </div>
                  <div className="break-words whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-white/50">
                    {previewProgress.thinking.slice(-800)}
                    {previewProgress.thinking.length > 800 && (
                      <span className="text-white/20">... (前面内容已省略)</span>
                    )}
                    <span className="inline-block w-[2px] h-[1em] bg-[var(--soft-accent)] align-middle animate-pulse ml-0.5" />
                  </div>
                </div>
              ) : null}

              {/* 已生成的chars段预览 */}
              {previewProgress.fields && Object.keys(previewProgress.fields).length > 0 && (
                <div className="soft-inset mb-4 max-h-32 overflow-y-auto rounded-lg p-3 border-[#22C55E]/20">
                  <div className="text-[10px] font-bold text-[#22C55E]/60 mb-2">已生成内容</div>
                  <div className="space-y-1">
                    {Object.entries(previewProgress.fields).map(([key, values]) => (
                      values && values.length > 0 ? (
                        <div key={key} className="flex items-start gap-2 text-[10px]">
                          <CheckCircle2 className="h-3 w-3 text-[#22C55E] mt-0.5 shrink-0" />
                          <span className="min-w-0 break-words text-white/60">
                            {SLOT_LABELS[key] || key}: <span className="text-white/80">{values.length} 项</span>
                          </span>
                        </div>
                      ) : null
                    ))}
                  </div>
                </div>
              )}

              {/* 取消按钮 */}
              <Button
                variant="secondary"
                onClick={() => {
                  stopPreviewStream();
                  setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
                  setLoadingPreview(false);
                }}
                className="soft-chip w-full cursor-pointer text-white/70 hover:bg-white/[0.08] hover:text-white"
              >
                <X className="mr-1.5 h-3.5 w-3.5" />
                Cancel Draft
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 2: Dialogue ── */}
        {step === 2 ? (
          <div data-testid="docs-init-dialogue-step" className="grid h-full min-h-0 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-[1.1fr_1.9fr]">

            {/* ─ Left: Project Goal Input ─ */}
            <ScrollArea data-testid="docs-init-input-scroll" className="soft-panel-subtle h-full min-h-0 rounded-xl">
              <div className="grid gap-4 p-5 text-sm">

                <div className="soft-raised rounded-lg px-4 py-2.5 text-[11px] leading-relaxed text-white/60">
                  <span className="text-[var(--soft-accent)] font-semibold">Tip</span>
                  <span className="mx-1.5 text-white/20">│</span>
                  Enter goal and have 1-3 dialogue rounds, then click Draft Plan to generate doc draft.
                </div>

                <label className="grid gap-1.5">
                  <span className="text-xs font-semibold tracking-wide text-[var(--soft-accent)]/70">Project Goal</span>
                  <input
                    data-testid="docs-init-goal-input"
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                    className="soft-inset rounded-lg px-3.5 py-2.5 text-sm text-white/90 placeholder:text-white/20 focus:border-[var(--soft-accent)]/30 focus:outline-none focus:ring-1 focus:ring-[var(--soft-accent)]/15 transition-colors duration-200"
                    placeholder="例：做一个简单的文件服务器（Node.js + TypeScript）"
                  />
                </label>

                <label className="grid gap-1.5">
                  <span className="text-xs font-semibold tracking-wide text-[var(--soft-accent)]/70">补 充 说 明<span className="ml-1 text-[10px] font-normal text-white/25">（可选）</span></span>
                  <textarea
                    data-testid="docs-init-message-input"
                    value={tingyiMessage}
                    onChange={(event) => setTingyiMessage(event.target.value)}
                    className="soft-inset min-h-[84px] rounded-lg px-3.5 py-2.5 text-sm text-white/90 placeholder:text-white/20 focus:border-[var(--soft-accent)]/30 focus:outline-none focus:ring-1 focus:ring-[var(--soft-accent)]/15 resize-none transition-colors duration-200"
                    placeholder="Can directly answer Architect follow-up，例如：1 本机进程 2 无鉴权 3 不限大小 4 需要 5 不压测"
                  />
                </label>

                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    data-testid="docs-init-run-dialogue"
                    disabled={dialoguing}
                    onClick={runDialogue}
                    className="cursor-pointer soft-raised text-white/90 hover:bg-white/[0.08] transition-all duration-200"
                  >
                    <span className="flex items-center gap-1.5">
                      <RiAiGenerate2 className="size-4" />
                      {dialoguing ? 'Dialoguing...' : '发 起 奏 对'}
                    </span>
                  </Button>
                </div>

                {/* Discussion Status */}
                <div className="soft-raised rounded-lg px-4 py-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-white/70">
                    <div className={`h-2 w-2 rounded-full ${phaseReady ? 'bg-[#22C55E]' : 'bg-[var(--soft-accent)] animate-pulse'}`} />
                    廷 议 状 态
                  </div>
                  <div className="mt-2 text-[11px] text-white/40">
                    阶段：
                    <span
                      data-testid="docs-init-phase-status"
                      className={phaseReady ? 'text-[#22C55E]' : 'text-[var(--soft-accent)]'}
                    >
                      {phaseReady ? 'Can draft plan' : 'Filling key info'}
                    </span>
                  </div>
                  <div data-testid="docs-init-unresolved-list" className="mt-2.5">
                    <div className="text-[10px] tracking-wide text-white/30 mb-1.5">待补充事项</div>
                    <div className="flex flex-wrap gap-1.5">
                      {dialogueMeta.unresolved_slots.length === 0 ? (
                        <span className="soft-chip rounded-md px-2.5 py-1 text-[10px] font-semibold text-[#22C55E]">
                          ✓ Ready
                        </span>
                      ) : (
                        dialogueMeta.unresolved_slots.map((slotId) => (
                          <span
                            key={slotId}
                            className="soft-chip rounded-md px-2.5 py-1 text-[10px] text-[var(--soft-accent)]/70"
                          >
                            {SLOT_LABELS[slotId] || slotId}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                {/* Advanced Options */}
                <details className="soft-inset group rounded-lg px-4 py-3">
                  <summary className="cursor-pointer text-xs font-semibold text-white/50 hover:text-white/80 transition-colors duration-200 select-none">
                    高 级 条 令<span className="ml-1 text-[10px] font-normal text-white/20">（Optional）</span>
                  </summary>
                  <div className="mt-4 grid gap-3">
                    {([
                      ['In Scope', inScope, setInScope, '每行一项'],
                      ['Out of Scope', outOfScope, setOutOfScope, '每行一项'],
                      ['Constraints', constraints, setConstraints, '建议 3-5 行'],
                      ['Definition of Done', definitionOfDone, setDefinitionOfDone, '勘验命令或验收条令'],
                    ] as const).map(([label, value, setter, ph]) => (
                      <label key={label} className="grid gap-1.5">
                        <span className="text-[11px] text-white/40">{label}</span>
                        <textarea
                          value={value}
                          onChange={(event) => setter(event.target.value)}
                          className="soft-inset min-h-[64px] rounded-lg px-3 py-2 text-xs text-white/80 placeholder:text-white/[0.15] focus:border-[var(--soft-accent)]/25 focus:outline-none resize-none transition-colors duration-200"
                          placeholder={ph}
                        />
                      </label>
                    ))}
                    <label className="grid gap-1.5">
                      <span className="text-[11px] text-white/40">Plan Draft (can override)</span>
                      <textarea
                        value={backlog}
                        onChange={(event) => {
                          setBacklog(event.target.value);
                          setTiaochenDraft(splitLines(event.target.value));
                        }}
                        className="soft-inset min-h-[64px] rounded-lg px-3 py-2 text-xs text-white/80 placeholder:text-white/[0.15] focus:border-[var(--soft-accent)]/25 focus:outline-none resize-none transition-colors duration-200"
                        placeholder="One task per line"
                      />
                    </label>
                  </div>
                </details>
              </div>
            </ScrollArea>

            {/* ─ Right: Dialogue Record + Plan ─ */}
            <div data-testid="docs-init-dialogue-right" className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-3 overflow-hidden">

              {/* Dialogue Record */}
              <div data-testid="docs-init-dialogue-record" className="soft-panel-subtle flex min-h-0 flex-col overflow-hidden rounded-xl p-4">
                <div className="flex items-center justify-between mb-3 flex-shrink-0">
                  <div className="flex items-center gap-2.5">
                    <div className="h-5 w-0.5 rounded-full bg-[var(--soft-accent)]/30" />
                    <span className="text-xs font-bold tracking-widest text-[var(--soft-accent)]/60">奏 对 记 录</span>
                  </div>
                  <span className="text-[10px] text-white/25 font-mono">{dialogueTurns.length} 轮</span>
                </div>
                <ScrollArea className="flex-1" style={{ minHeight: '150px' }}>
                  <div className="grid gap-3 pr-2">
                    {dialogueTurns.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-12 text-center">
                        <Landmark className="size-8 mb-3 text-white/[0.15]" />
                        <div className="text-xs text-white/25">Discussion not started</div>
                        <div className="text-[10px] text-white/[0.12] mt-1">Enter project goal, click Start Dialogue to start discussion</div>
                      </div>
                    ) : (
                      dialogueTurns.map((turn, index) => {
                        const isUser = turn.role === 'user';
                        const isStreamingTurn = turn.role === 'assistant' && index === streamingIndexRef.current && dialoguing;

                        return (
                          <div
                            key={`${turn.role}-${index}`}
                            className={`rounded-xl border px-4 py-3 text-xs transition-all duration-200 ${
                              isUser
                                ? 'soft-raised ml-8'
                                : 'soft-inset mr-8'
                            }`}
                          >
                            <div className={`mb-1.5 ${isUser ? 'text-right' : 'text-left'}`}>
                              <span className={`inline-flex items-center gap-1 soft-chip rounded-md px-2 py-0.5 text-[9px] font-bold tracking-widest ${
                                isUser
                                  ? 'text-[var(--soft-accent)]'
                                  : 'text-white/60'
                              }`}>
                                {isUser ? 'User' : 'Architect'}
                              </span>
                            </div>

                            <div className="break-words whitespace-pre-wrap leading-relaxed">
                              {isStreamingTurn ? (
                                <div className="space-y-2">
                                  {turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || '') ? (
                                    <div className="soft-inset rounded-lg px-3 py-2">
                                      <div className="flex items-center gap-1.5 mb-1">
                                        <Brain className="size-3 text-[var(--soft-accent)]/60" />
                                        <span className="text-[9px] font-bold tracking-widest text-[var(--soft-accent)]/60">自 言 自 语</span>
                                      </div>
                                      <div className="max-h-[140px] overflow-y-auto break-words text-[11px] italic leading-relaxed text-white/50">
                                        {turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || '')}
                                        <span className="inline-block w-[2px] h-[1em] bg-[var(--soft-accent)] align-middle animate-pulse ml-0.5" />
                                      </div>
                                    </div>
                                  ) : null}
                                  {turn.rawContent ? (
                                    <div className="soft-inset rounded-lg px-3 py-2">
                                      <div className="text-[9px] font-bold tracking-widest text-white/40 mb-0.5">Receiving response...</div>
                                      <div className="break-words text-[10px] italic text-white/30">
                                        Received {turn.rawContent.length} chars
                                      </div>
                                    </div>
                                  ) : null}
                                    <span className="text-white/40 text-[10px] flex items-center gap-2">
                                    <span className="inline-flex gap-0.5">
                                      <span className="inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce" style={{ animationDelay: '0ms' }} />
                                      <span className="inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce" style={{ animationDelay: '150ms' }} />
                                      <span className="inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce" style={{ animationDelay: '300ms' }} />
                                    </span>
                                    {turn.reasoning
                                      ? 'Architect drafting response...'
                                      : buildStreamingThoughtPreview(turn.rawContent || '')
                                        ? 'Real-time streaming content'
                                        : turn.rawContent
                                          ? 'Response streaming...'
                                          : 'Architect thinking...'}
                                  </span>
                                </div>
                              ) : (
                                <>
                                  {turn.reasoning ? (
                                    <details className="mb-2">
                                      <summary className="text-[9px] tracking-widest text-[var(--soft-accent)]/35 cursor-pointer hover:text-[var(--soft-accent)]/60 transition-colors duration-200 select-none">
                                        Expand reasoning chain
                                      </summary>
                                      <div className="mt-1.5 max-h-[140px] overflow-y-auto break-words soft-inset rounded-lg px-3 py-2 text-[11px] italic leading-relaxed text-white/45">
                                        {turn.reasoning}
                                      </div>
                                    </details>
                                  ) : null}
                                  <div className={isUser ? 'break-words text-white/85' : 'break-words text-white/80'}>
                                    {turn.content || (() => {
                                      if (!turn.rawContent) return 'Plan updated based on discussion.';
                                      try {
                                        const parsed = JSON.parse(turn.rawContent);
                                        return parsed.reply || turn.rawContent;
                                      } catch {
                                        return turn.rawContent;
                                      }
                                    })()}
                                  </div>
                                </>
                              )}
                            </div>
                            {turn.questions && turn.questions.length > 0 ? (
                              <div className="mt-2.5 border-t border-white/[0.08] pt-2 text-[11px] text-white/60 space-y-0.5">
                                {turn.questions.map((question, qIndex) => (
                                  <div key={`${index}-${qIndex}`} className="flex min-w-0 gap-1.5">
                                    <span className="text-[var(--soft-accent)]/35 font-mono">{qIndex + 1}.</span>
                                    <span className="min-w-0 break-words">{question}</span>
                                  </div>
                                ))}
                              </div>
                            ) : null}
                          </div>
                        );
                      })
                    )}
                  </div>
                </ScrollArea>
              </div>

              {/* 条陈·奏章 */}
              <div data-testid="docs-init-plan-draft" className="soft-raised max-h-[22vh] overflow-hidden rounded-xl p-4 border-[#22C55E]/20">
                <div className="flex items-center gap-2.5 mb-2">
                  <div className="h-4 w-0.5 rounded-full bg-[#22C55E]/30" />
                  <span className="text-xs font-bold tracking-widest text-[#22C55E]/60">Plan · Draft</span>
                  <span className="text-[9px] text-[#22C55E]/25 tracking-wide">Draft</span>
                </div>
                <div className="grid gap-0.5 text-xs text-white/60 max-h-[18vh] overflow-y-auto pr-1">
                  {tiaochenDraft.length === 0 ? (
                    <div className="text-white/20 text-[11px] py-2">Plan not yet generated, please start dialogue first.</div>
                  ) : (
                    tiaochenDraft.map((item, index) => (
                      <div key={`${index}-${item}`} className="flex gap-2 py-0.5 rounded px-2 hover:bg-white/[0.04] transition-colors duration-150">
                        <span className="text-[#22C55E]/35 font-mono text-[10px] w-4 text-right flex-shrink-0">{index + 1}</span>
                        <span className="min-w-0 break-words">{item}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {/* ── Step 3: Approve ── */}
        {step === 3 ? (
          <div data-testid="docs-init-approve-step" className="grid h-full min-h-0 grid-rows-[auto_auto_minmax(0,1fr)] gap-3 overflow-hidden">
            <div className="soft-raised flex min-w-0 items-center gap-3 rounded-lg px-4 py-3 text-xs text-white/60">
              <ScrollText className="size-5 text-[var(--soft-accent)]/70 flex-shrink-0" />
              <div className="min-w-0">
                <div className="break-all text-white/70">Plan doc directory：<span className="text-[var(--soft-accent)] font-semibold">{preview?.target_root || 'docs'}</span></div>
                <div className="text-white/30 text-[10px] mt-0.5">Click Approve after confirmation to finalize docs.</div>
              </div>
            </div>
            {tiaochenDraft.length > 0 ? (
              <div className="soft-raised rounded-lg px-4 py-3 text-xs text-white/60 border-[#22C55E]/15">
                <div className="mb-2 font-bold tracking-widest text-[#22C55E]/60 text-[11px]">Plan · Draft</div>
                <div className="space-y-0.5">
                  {tiaochenDraft.map((item, index) => (
                    <div key={`${index}-${item}`} className="flex min-w-0 gap-2">
                      <span className="text-[#22C55E]/35 font-mono text-[10px]">{index + 1}.</span>
                      <span className="min-w-0 break-words">{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            <ScrollArea data-testid="docs-init-preview-scroll" className="soft-panel-subtle h-full min-h-0 rounded-xl">
              <div className="grid gap-4 p-4">
                {preview?.files.map((file, index) => (
                  <div key={file.path} className="soft-inset min-w-0 overflow-hidden rounded-lg">
                    <div className="flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] bg-white/[0.02] px-4 py-2 text-[11px]">
                      <span className="min-w-0 break-all font-mono text-[var(--soft-accent)]/50">{file.path}</span>
                      {file.exists ? (
                        <span className="soft-chip rounded px-2 py-0.5 text-[9px] text-[var(--soft-accent)]">已存在</span>
                      ) : (
                        <span className="soft-chip rounded px-2 py-0.5 text-[9px] text-[#22C55E]">新建</span>
                      )}
                    </div>
                    <textarea
                      value={file.content}
                      onChange={(event) => updatePreviewFile(index, event.target.value)}
                      className="min-h-[180px] w-full resize-y bg-transparent px-4 py-3 font-mono text-xs leading-relaxed text-white/70 outline-none"
                    />
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        ) : null}

        </div>

        {/* ── Footer ── */}
        <DialogFooter data-testid="docs-init-footer" className="flex flex-row flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3">
          <div className="flex flex-wrap gap-2">
            {step === 3 ? (
              <Button
                variant="secondary"
                onClick={() => setStep(step - 1)}
                className="cursor-pointer soft-chip text-white/60 hover:bg-white/[0.06] hover:text-white/80 text-xs transition-colors duration-200"
              >
                <span className="flex items-center gap-1"><ChevronLeft className="size-3.5" />上一步</span>
              </Button>
            ) : null}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="cursor-pointer text-white/30 hover:text-white/60 text-xs transition-colors duration-200"
            >
              Close
            </Button>
            {step === 2 ? (
              <Button
                data-testid="docs-init-build-preview"
                onClick={buildPreview}
                disabled={loadingPreview}
                className="cursor-pointer soft-raised text-white/90 text-xs font-bold tracking-wide transition-all duration-200"
              >
                <span className="flex items-center gap-1.5">
                  <ScrollText className="size-3.5" />
                  {loadingPreview ? 'Drafting...' : '拟 定 条 陈'}
                </span>
              </Button>
            ) : null}
            {step === 3 ? (
              <Button
                data-testid="docs-init-apply"
                onClick={applyDocs}
                disabled={applying}
                className="cursor-pointer soft-raised border-red-500/20 text-white/90 text-xs font-bold tracking-wide transition-all duration-200"
              >
                <span className="flex items-center gap-1.5">
                  <Stamp className="size-3.5" />
                  {applying ? 'Approving...' : 'Approve'}
                </span>
              </Button>
            ) : null}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
