import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RiAiGenerate2 } from 'react-icons/ri';
import { Landmark, ScrollText, Stamp, Brain, ChevronLeft, Loader2, X, FileText, CheckCircle2 } from 'lucide-react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';
import { useSSEStream, type SSERawEvent } from '@/hooks/useSSEStream';
import {
  normalizeDocsInitPreviewPayload,
  type DocsInitPreview,
} from '@/app/utils/docsInitPreview';
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
const INVALID_PREVIEW_ERROR = '条陈预览数据不完整，请重新拟定条陈。';
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

  // 拟定条陈流式进度状态
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

  const onRawEvent = useCallback((event: SSERawEvent) => {
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
  const onPreviewRawEvent = useCallback((event: SSERawEvent) => {
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
    useSSEStream({ onRawEvent: onPreviewRawEvent, onComplete: onPreviewComplete, onError: onPreviewError });

  const onComplete = useCallback(
    (data: Record<string, unknown>) => {
      const reply = String(data.reply || '').trim();
      const questions = ((data.questions || []) as string[]).map((s) => String(s).trim()).filter(Boolean);
      const assistantContent = reply || (questions.length ? questions.join('\n') : '臣已据廷议更新条陈。');

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
    toast.error(error || '奏对失败。');
    setDialoguing(false);
  }, []);

  const { isStreaming, startStream, stopStream } = useSSEStream({ onRawEvent, onComplete, onError });

  const runDialogue = async () => {
    const message = tingyiMessage.trim() || goal.trim();
    if (!message) {
      toast.error('请先输入廷议内容或圣意目标。');
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

    startStream('/docs/init/dialogue/stream', {
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

    startPreviewStream('/docs/init/preview/stream', {
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
      const res = await apiFetch('/docs/init/apply', {
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
        throw new Error(detail?.detail || '用印写入失败。');
      }
      toast.success('条陈已批红并用印');
      onApplied?.();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : '用印写入失败。');
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
      <DialogContent data-testid="docs-init-dialog" className="max-w-[98vw] w-[98vw] max-h-[96vh] overflow-hidden border border-[#CA8A04]/25 bg-[#080812] text-[#F8FAFC] shadow-[0_0_60px_rgba(202,138,4,0.12),_0_0_1px_rgba(202,138,4,0.4)] [background-image:radial-gradient(rgba(202,138,4,0.04)_1px,transparent_1px)] [background-size:24px_24px]">

        {/* ── Header ── */}
        <DialogHeader className="relative pb-3 border-b border-[#CA8A04]/10">
          <div className="absolute inset-x-0 bottom-0 h-px shadow-[0_0_8px_rgba(202,138,4,0.3)] bg-[#CA8A04]/15" />
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-3">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-[#CA8A04]/25 bg-[#CA8A04]/8 shadow-[0_0_20px_rgba(202,138,4,0.20),_inset_0_0_8px_rgba(202,138,4,0.08)]">
                  <Landmark className="size-4.5 text-[#CA8A04] drop-shadow-[0_0_4px_rgba(202,138,4,0.5)]" />
                </div>
                <div>
                  <DialogTitle className="text-lg font-bold tracking-wide">
                    <span className="text-[#CA8A04] [text-shadow:0_0_12px_rgba(202,138,4,0.4),_0_0_4px_rgba(202,138,4,0.2)]">政 事 堂</span>
                    <span className="ml-2.5 text-xs font-normal tracking-widest text-[#CA8A04]/40">中书令廷议规划</span>
                  </DialogTitle>
                  <DialogDescription className="mt-0.5 text-[11px] text-[#F8FAFC]/30">
                    {docsMissing
                      ? '当前疆域缺少 docs/ 卷宗，请先在政事堂廷议立卷。'
                      : '奏对问答 → 拟定条陈 → 批红用印 ┃ 全程以中书令角色绑定 LLM'}
                  </DialogDescription>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 pt-1">
              <div className="flex items-center gap-2 rounded-lg border border-[#F8FAFC]/8 bg-[#F8FAFC]/[0.03] px-3 py-1.5">
                <div className={`h-1.5 w-1.5 rounded-full ${phaseReady ? 'bg-[#22C55E] shadow-[0_0_8px_rgba(34,197,94,0.6)]' : 'bg-[#CA8A04] shadow-[0_0_8px_rgba(202,138,4,0.5)] animate-pulse'}`} />
                <span className="text-[10px] tracking-widest text-[#F8FAFC]/60">
                  {phaseReady ? '廷议齐备 · 可拟条陈' : '廷议进行中'}
                </span>
              </div>
              <span className="text-[10px] text-[#CA8A04]/25 font-mono">
                {step === 3 ? 'II' : 'I'} / II
              </span>
            </div>
          </div>
          {workspace ? (
            <div className="mt-2 text-[10px] tracking-wider text-[#F8FAFC]/20 font-mono">
              疆域 ▸ {workspace}
            </div>
          ) : null}
        </DialogHeader>

        {error ? (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-2.5 text-xs text-red-200 shadow-[inset_0_0_20px_rgba(239,68,68,0.05)]">
            <span className="mr-2 text-red-400">!</span>{error}
          </div>
        ) : null}

        {/* ── 拟定条陈进度弹窗 ── */}
        {previewProgress.open && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
            <div className="w-full max-w-md rounded-xl border border-[#CA8A04]/20 bg-[#0C0C1E]/95 p-6 shadow-[0_0_60px_rgba(202,138,4,0.15)]">
              <div className="flex items-center gap-3 mb-4">
                <div className="relative">
                  <div className="h-10 w-10 rounded-full border-2 border-[#CA8A04]/30 border-t-[#CA8A04] animate-spin" />
                  <ScrollText className="absolute inset-0 m-auto h-4 w-4 text-[#CA8A04]" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-[#CA8A04]">拟定条陈中...</h3>
                  <p className="text-[11px] text-[#F8FAFC]/50">中书令正在为您生成文档</p>
                </div>
              </div>

              {/* 进度条 */}
              <div className="mb-4">
                <div className="h-1.5 w-full rounded-full bg-[#F8FAFC]/10 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[#CA8A04] to-[#F59E0B] transition-all duration-500"
                    style={{ width: `${previewProgress.progress}%` }}
                  />
                </div>
                <div className="mt-1 flex justify-between text-[10px] text-[#F8FAFC]/40">
                  <span>{previewProgress.progress}%</span>
                  <span>{previewProgress.stage === 'llm_start' ? 'AI思考中' : '处理中'}</span>
                </div>
              </div>

              {/* 当前步骤 */}
              <div className="mb-4 rounded-lg border border-[#F8FAFC]/5 bg-[#F8FAFC]/[0.02] p-3">
                <div className="flex items-center gap-2 text-xs text-[#F8FAFC]/70">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-[#CA8A04]" />
                  {previewProgress.message}
                </div>              </div>

              {/* 实时思考内容 */}
              {previewProgress.thinking ? (
                <div className="mb-4 max-h-48 overflow-y-auto rounded-lg border border-[#CA8A04]/10 bg-[#CA8A04]/5 p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <Brain className="h-3 w-3 text-[#CA8A04]" />
                    <span className="text-[10px] font-bold text-[#CA8A04]/60">中书令思考中...</span>
                  </div>
                  <div className="text-[11px] text-[#F8FAFC]/50 leading-relaxed font-mono whitespace-pre-wrap">
                    {previewProgress.thinking.slice(-800)}
                    {previewProgress.thinking.length > 800 && (
                      <span className="text-[#F8FAFC]/20">... (前面内容已省略)</span>
                    )}
                    <span className="inline-block w-[2px] h-[1em] bg-[#CA8A04] align-middle animate-pulse ml-0.5" />
                  </div>
                </div>
              ) : null}

              {/* 已生成的字段预览 */}
              {previewProgress.fields && Object.keys(previewProgress.fields).length > 0 && (
                <div className="mb-4 max-h-32 overflow-y-auto rounded-lg border border-[#22C55E]/10 bg-[#22C55E]/5 p-3">
                  <div className="text-[10px] font-bold text-[#22C55E]/60 mb-2">已生成内容</div>
                  <div className="space-y-1">
                    {Object.entries(previewProgress.fields).map(([key, values]) => (
                      values && values.length > 0 ? (
                        <div key={key} className="flex items-start gap-2 text-[10px]">
                          <CheckCircle2 className="h-3 w-3 text-[#22C55E] mt-0.5 shrink-0" />
                          <span className="text-[#F8FAFC]/60">
                            {SLOT_LABELS[key] || key}: <span className="text-[#F8FAFC]/80">{values.length} 项</span>
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
                className="w-full cursor-pointer border-[#F8FAFC]/10 bg-[#F8FAFC]/[0.05] text-[#F8FAFC]/70 hover:bg-[#F8FAFC]/[0.08] hover:text-[#F8FAFC]"
              >
                <X className="mr-1.5 h-3.5 w-3.5" />
                取消拟定
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 2: 奏对 ── */}
        {step === 2 ? (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-[1.1fr_1.9fr]">

            {/* ─ Left: 圣意输入 ─ */}
            <ScrollArea className="h-[74vh] rounded-xl border border-[#CA8A04]/12 bg-[#12112A]/80 backdrop-blur-sm shadow-[inset_0_1px_0_rgba(202,138,4,0.06)]">
              <div className="grid gap-4 p-5 text-sm">

                <div className="rounded-lg border border-[#CA8A04]/15 bg-[#CA8A04]/5 px-4 py-2.5 text-[11px] leading-relaxed text-[#F8FAFC]/60">
                  <span className="text-[#CA8A04]/80 font-semibold">御笔提示</span>
                  <span className="mx-1.5 text-[#CA8A04]/20">│</span>
                  先输入目标并进行 1~3 轮奏对，再点击「拟定条陈」生成落卷草案。
                </div>

                <label className="grid gap-1.5">
                  <span className="text-xs font-semibold tracking-wide text-[#CA8A04]/70">圣 意 目 标</span>
                  <input
                    data-testid="docs-init-goal-input"
                    value={goal}
                    onChange={(event) => setGoal(event.target.value)}
                    className="rounded-lg border border-[#F8FAFC]/8 bg-[#F8FAFC]/[0.03] px-3.5 py-2.5 text-sm text-[#F8FAFC]/90 placeholder:text-[#F8FAFC]/20 focus:border-[#CA8A04]/30 focus:outline-none focus:ring-1 focus:ring-[#CA8A04]/15 transition-colors duration-200"
                    placeholder="例：做一个简单的文件服务器（Node.js + TypeScript）"
                  />
                </label>

                <label className="grid gap-1.5">
                  <span className="text-xs font-semibold tracking-wide text-[#CA8A04]/70">补 充 说 明<span className="ml-1 text-[10px] font-normal text-[#CA8A04]/30">（可选）</span></span>
                  <textarea
                    data-testid="docs-init-message-input"
                    value={tingyiMessage}
                    onChange={(event) => setTingyiMessage(event.target.value)}
                    className="min-h-[84px] rounded-lg border border-[#F8FAFC]/8 bg-[#F8FAFC]/[0.03] px-3.5 py-2.5 text-sm text-[#F8FAFC]/90 placeholder:text-[#F8FAFC]/20 focus:border-[#CA8A04]/30 focus:outline-none focus:ring-1 focus:ring-[#CA8A04]/15 resize-none transition-colors duration-200"
                    placeholder="可直接回答中书令追问，例如：1 本机进程 2 无鉴权 3 不限大小 4 需要 5 不压测"
                  />
                </label>

                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    data-testid="docs-init-run-dialogue"
                    disabled={dialoguing}
                    onClick={runDialogue}
                    className="cursor-pointer border-[#CA8A04]/30 bg-[#CA8A04]/10 text-[#F8FAFC]/90 hover:bg-[#CA8A04]/20 hover:border-[#CA8A04]/50 hover:shadow-[0_0_20px_rgba(202,138,4,0.20)] shadow-[0_0_12px_rgba(202,138,4,0.10)] transition-all duration-200"
                  >
                    <span className="flex items-center gap-1.5">
                      <RiAiGenerate2 className="size-4" />
                      {dialoguing ? '奏对中…' : '发 起 奏 对'}
                    </span>
                  </Button>
                </div>

                {/* 廷议状态 */}
                <div className="rounded-lg border border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] px-4 py-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-[#F8FAFC]/70">
                    <div className={`h-2 w-2 rounded-full ${phaseReady ? 'bg-[#22C55E] shadow-[0_0_8px_rgba(34,197,94,0.5)]' : 'bg-[#CA8A04] shadow-[0_0_8px_rgba(202,138,4,0.4)] animate-pulse'}`} />
                    廷 议 状 态
                  </div>
                  <div className="mt-2 text-[11px] text-[#F8FAFC]/40">
                    阶段：
                    <span
                      data-testid="docs-init-phase-status"
                      className={phaseReady ? 'text-[#22C55E]' : 'text-[#CA8A04]'}
                    >
                      {phaseReady ? '可拟定条陈' : '补齐关键信息中'}
                    </span>
                  </div>
                  <div data-testid="docs-init-unresolved-list" className="mt-2.5">
                    <div className="text-[10px] tracking-wide text-[#F8FAFC]/30 mb-1.5">待补充事项</div>
                    <div className="flex flex-wrap gap-1.5">
                      {dialogueMeta.unresolved_slots.length === 0 ? (
                        <span className="rounded-md border border-[#22C55E]/25 bg-[#22C55E]/10 px-2.5 py-1 text-[10px] font-semibold text-[#22C55E] shadow-[0_0_8px_rgba(34,197,94,0.12)]">
                          ✓ 已齐备
                        </span>
                      ) : (
                        dialogueMeta.unresolved_slots.map((slotId) => (
                          <span
                            key={slotId}
                            className="rounded-md border border-[#CA8A04]/20 bg-[#CA8A04]/10 px-2.5 py-1 text-[10px] text-[#CA8A04]/70"
                          >
                            {SLOT_LABELS[slotId] || slotId}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                {/* 高级条令 */}
                <details className="group rounded-lg border border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] px-4 py-3">
                  <summary className="cursor-pointer text-xs font-semibold text-[#F8FAFC]/50 hover:text-[#F8FAFC]/80 transition-colors duration-200 select-none">
                    高 级 条 令<span className="ml-1 text-[10px] font-normal text-[#F8FAFC]/20">（可选展开）</span>
                  </summary>
                  <div className="mt-4 grid gap-3">
                    {([
                      ['纳入范围', inScope, setInScope, '每行一项'],
                      ['排除范围', outOfScope, setOutOfScope, '每行一项'],
                      ['约束条令', constraints, setConstraints, '建议 3-5 行'],
                      ['结案标准', definitionOfDone, setDefinitionOfDone, '勘验命令或验收条令'],
                    ] as const).map(([label, value, setter, ph]) => (
                      <label key={label} className="grid gap-1.5">
                        <span className="text-[11px] text-[#F8FAFC]/40">{label}</span>
                        <textarea
                          value={value}
                          onChange={(event) => setter(event.target.value)}
                          className="min-h-[64px] rounded-lg border border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] px-3 py-2 text-xs text-[#F8FAFC]/80 placeholder:text-[#F8FAFC]/15 focus:border-[#CA8A04]/25 focus:outline-none resize-none transition-colors duration-200"
                          placeholder={ph}
                        />
                      </label>
                    ))}
                    <label className="grid gap-1.5">
                      <span className="text-[11px] text-[#F8FAFC]/40">条陈草案（可手工覆盖）</span>
                      <textarea
                        value={backlog}
                        onChange={(event) => {
                          setBacklog(event.target.value);
                          setTiaochenDraft(splitLines(event.target.value));
                        }}
                        className="min-h-[64px] rounded-lg border border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] px-3 py-2 text-xs text-[#F8FAFC]/80 placeholder:text-[#F8FAFC]/15 focus:border-[#CA8A04]/25 focus:outline-none resize-none transition-colors duration-200"
                        placeholder="每行一条任务"
                      />
                    </label>
                  </div>
                </details>
              </div>
            </ScrollArea>

            {/* ─ Right: 奏对记录 + 条陈 ─ */}
            <div className="grid grid-rows-[minmax(300px,1fr)_auto] gap-3 overflow-hidden" style={{ height: '74vh' }}>

              {/* 奏对记录 */}
              <div className="rounded-xl border border-[#00FFFF]/10 bg-[#0C0C1E]/80 backdrop-blur-sm p-4 flex flex-col overflow-hidden shadow-[inset_0_1px_0_rgba(0,255,255,0.04)]" style={{ minHeight: '200px' }}>
                <div className="flex items-center justify-between mb-3 flex-shrink-0">
                  <div className="flex items-center gap-2.5">
                    <div className="h-5 w-0.5 rounded-full bg-[#00FFFF]/30 shadow-[0_0_6px_rgba(0,255,255,0.3)]" />
                    <span className="text-xs font-bold tracking-widest text-[#00FFFF]/60">奏 对 记 录</span>
                  </div>
                  <span className="text-[10px] text-[#00FFFF]/25 font-mono">{dialogueTurns.length} 轮</span>
                </div>
                <ScrollArea className="flex-1" style={{ minHeight: '150px' }}>
                  <div className="grid gap-3 pr-2">
                    {dialogueTurns.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-12 text-center">
                        <Landmark className="size-8 mb-3 text-[#CA8A04]/20" />
                        <div className="text-xs text-[#F8FAFC]/25">廷议尚未开始</div>
                        <div className="text-[10px] text-[#F8FAFC]/12 mt-1">输入圣意目标，点击「发起奏对」开启廷议</div>
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
                                ? 'border-[#CA8A04]/20 bg-[#CA8A04]/5 ml-8'
                                : 'border-[#00FFFF]/12 bg-[#00FFFF]/5 mr-8'
                            }`}
                          >
                            <div className={`mb-1.5 ${isUser ? 'text-right' : 'text-left'}`}>
                              <span className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[9px] font-bold tracking-widest ${
                                isUser
                                  ? 'border-[#CA8A04]/30 bg-[#CA8A04]/12 text-[#CA8A04]'
                                  : 'border-[#00FFFF]/25 bg-[#00FFFF]/10 text-[#00FFFF]/80'
                              }`}>
                                {isUser ? '天 子' : '中 书 令'}
                              </span>
                            </div>

                            <div className="whitespace-pre-wrap leading-relaxed">
                              {isStreamingTurn ? (
                                <div className="space-y-2">
                                  {turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || '') ? (
                                    <div className="rounded-lg border border-[#CA8A04]/15 bg-[#CA8A04]/5 px-3 py-2">
                                      <div className="flex items-center gap-1.5 mb-1">
                                        <Brain className="size-3 text-[#CA8A04]/60" />
                                        <span className="text-[9px] font-bold tracking-widest text-[#CA8A04]/60">自 言 自 语</span>
                                      </div>
                                      <div className="text-[11px] text-[#F8FAFC]/50 italic leading-relaxed max-h-[140px] overflow-y-auto">
                                        {turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || '')}
                                        <span className="inline-block w-[2px] h-[1em] bg-[#CA8A04] align-middle animate-pulse ml-0.5" />
                                      </div>
                                    </div>
                                  ) : null}
                                  {turn.rawContent ? (
                                    <div className="rounded-lg border border-[#00FFFF]/10 bg-[#00FFFF]/5 px-3 py-2">
                                      <div className="text-[9px] font-bold tracking-widest text-[#00FFFF]/40 mb-0.5">奏答接收中…</div>
                                      <div className="text-[10px] text-[#F8FAFC]/30 italic">
                                        已接收 {turn.rawContent.length} 字
                                      </div>
                                    </div>
                                  ) : null}
                                    <span className="text-[#00FFFF]/50 text-[10px] flex items-center gap-2">
                                    <span className="inline-flex gap-0.5">
                                      <span className="inline-block w-1 h-1 rounded-full bg-[#00FFFF]/60 animate-bounce" style={{ animationDelay: '0ms' }} />
                                      <span className="inline-block w-1 h-1 rounded-full bg-[#00FFFF]/60 animate-bounce" style={{ animationDelay: '150ms' }} />
                                      <span className="inline-block w-1 h-1 rounded-full bg-[#00FFFF]/60 animate-bounce" style={{ animationDelay: '300ms' }} />
                                    </span>
                                    {turn.reasoning
                                      ? '中书令正在草拟奏答…'
                                      : buildStreamingThoughtPreview(turn.rawContent || '')
                                        ? '实时流式内容'
                                        : turn.rawContent
                                          ? '奏答流式接收中'
                                          : '中书令正在思考…'}
                                  </span>
                                </div>
                              ) : (
                                <>
                                  {turn.reasoning ? (
                                    <details className="mb-2">
                                      <summary className="text-[9px] tracking-widest text-[#CA8A04]/35 cursor-pointer hover:text-[#CA8A04]/60 transition-colors duration-200 select-none">
                                        展开思维链
                                      </summary>
                                      <div className="mt-1.5 rounded-lg border border-[#CA8A04]/10 bg-[#CA8A04]/5 px-3 py-2 text-[11px] text-[#F8FAFC]/45 italic leading-relaxed max-h-[140px] overflow-y-auto">
                                        {turn.reasoning}
                                      </div>
                                    </details>
                                  ) : null}
                                  <div className={isUser ? 'text-[#F8FAFC]/85' : 'text-[#F8FAFC]/80'}>
                                    {turn.content || (() => {
                                      if (!turn.rawContent) return '臣已据廷议更新条陈。';
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
                              <div className="mt-2.5 border-t border-[#312E81]/30 pt-2 text-[11px] text-[#F8FAFC]/60 space-y-0.5">
                                {turn.questions.map((question, qIndex) => (
                                  <div key={`${index}-${qIndex}`} className="flex gap-1.5">
                                    <span className="text-[#CA8A04]/35 font-mono">{qIndex + 1}.</span>
                                    <span>{question}</span>
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
              <div className="rounded-xl border border-[#22C55E]/10 bg-[#0C0C1E]/60 backdrop-blur-sm p-4 shadow-[inset_0_1px_0_rgba(34,197,94,0.04)]">
                <div className="flex items-center gap-2.5 mb-2">
                  <div className="h-4 w-0.5 rounded-full bg-[#22C55E]/30 shadow-[0_0_6px_rgba(34,197,94,0.3)]" />
                  <span className="text-xs font-bold tracking-widest text-[#22C55E]/60">条 陈 · 奏 章</span>
                  <span className="text-[9px] text-[#22C55E]/25 tracking-wide">草拟</span>
                </div>
                <div className="grid gap-0.5 text-xs text-[#F8FAFC]/60 max-h-[18vh] overflow-y-auto pr-1">
                  {tiaochenDraft.length === 0 ? (
                    <div className="text-[#F8FAFC]/20 text-[11px] py-2">尚未生成条陈，请先进行奏对。</div>
                  ) : (
                    tiaochenDraft.map((item, index) => (
                      <div key={`${index}-${item}`} className="flex gap-2 py-0.5 rounded px-2 hover:bg-[#22C55E]/5 transition-colors duration-150">
                        <span className="text-[#22C55E]/35 font-mono text-[10px] w-4 text-right flex-shrink-0">{index + 1}</span>
                        <span>{item}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {/* ── Step 3: 批红 ── */}
        {step === 3 ? (
          <div className="grid gap-3 min-h-0 overflow-hidden">
            <div className="rounded-lg border border-[#CA8A04]/20 bg-[#CA8A04]/5 px-4 py-3 text-xs text-[#F8FAFC]/60 flex items-center gap-3">
              <ScrollText className="size-5 text-[#CA8A04]/70 flex-shrink-0" />
              <div>
                <div className="text-[#F8FAFC]/70">条陈卷宗目录：<span className="text-[#CA8A04] font-semibold">{preview?.target_root || 'docs'}</span></div>
                <div className="text-[#F8FAFC]/30 text-[10px] mt-0.5">确认无误后点击「批红 / 用印」，正式落卷。</div>
              </div>
            </div>
            {tiaochenDraft.length > 0 ? (
              <div className="rounded-lg border border-[#22C55E]/12 bg-[#22C55E]/5 px-4 py-3 text-xs text-[#F8FAFC]/60">
                <div className="mb-2 font-bold tracking-widest text-[#22C55E]/60 text-[11px]">条 陈 · 奏 章</div>
                <div className="space-y-0.5">
                  {tiaochenDraft.map((item, index) => (
                    <div key={`${index}-${item}`} className="flex gap-2">
                      <span className="text-[#22C55E]/35 font-mono text-[10px]">{index + 1}.</span>
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            <ScrollArea className="h-[60vh] rounded-xl border border-[#00FFFF]/10 bg-[#0C0C1E]/80 backdrop-blur-sm">
              <div className="grid gap-4 p-4">
                {preview?.files.map((file, index) => (
                  <div key={file.path} className="rounded-lg border border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] overflow-hidden">
                    <div className="flex items-center justify-between border-b border-[#F8FAFC]/6 bg-[#F8FAFC]/[0.02] px-4 py-2 text-[11px]">
                      <span className="text-[#00FFFF]/50 font-mono">{file.path}</span>
                      {file.exists ? (
                        <span className="rounded border border-[#CA8A04]/20 bg-[#CA8A04]/10 px-2 py-0.5 text-[9px] text-[#CA8A04]">已存在</span>
                      ) : (
                        <span className="rounded border border-[#22C55E]/20 bg-[#22C55E]/10 px-2 py-0.5 text-[9px] text-[#22C55E]">新建</span>
                      )}
                    </div>
                    <textarea
                      value={file.content}
                      onChange={(event) => updatePreviewFile(index, event.target.value)}
                      className="min-h-[180px] w-full resize-y bg-transparent px-4 py-3 text-xs text-[#F8FAFC]/70 outline-none font-mono leading-relaxed"
                    />
                  </div>
                ))}
              </div>
            </ScrollArea>
          </div>
        ) : null}

        {/* ── Footer ── */}
        <DialogFooter className="flex flex-row items-center justify-between border-t border-[#F8FAFC]/6 pt-3">
          <div className="flex gap-2">
            {step === 3 ? (
              <Button
                variant="secondary"
                onClick={() => setStep(step - 1)}
                className="cursor-pointer border-[#F8FAFC]/8 bg-[#F8FAFC]/[0.03] text-[#F8FAFC]/60 hover:bg-[#F8FAFC]/[0.06] hover:text-[#F8FAFC]/80 text-xs transition-colors duration-200"
              >
                <span className="flex items-center gap-1"><ChevronLeft className="size-3.5" />上一步</span>
              </Button>
            ) : null}
          </div>
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={() => onOpenChange(false)}
              className="cursor-pointer text-[#F8FAFC]/30 hover:text-[#F8FAFC]/60 text-xs transition-colors duration-200"
            >
              退朝
            </Button>
            {step === 2 ? (
              <Button
                data-testid="docs-init-build-preview"
                onClick={buildPreview}
                disabled={loadingPreview}
                className="cursor-pointer border border-[#CA8A04]/30 bg-[#CA8A04]/15 text-[#F8FAFC]/90 hover:bg-[#CA8A04]/25 hover:shadow-[0_0_24px_rgba(202,138,4,0.20)] shadow-[0_0_16px_rgba(202,138,4,0.10)] text-xs font-bold tracking-wide transition-all duration-200"
              >
                <span className="flex items-center gap-1.5">
                  <ScrollText className="size-3.5" />
                  {loadingPreview ? '拟稿中…' : '拟 定 条 陈'}
                </span>
              </Button>
            ) : null}
            {step === 3 ? (
              <Button
                data-testid="docs-init-apply"
                onClick={applyDocs}
                disabled={applying}
                className="cursor-pointer border border-red-500/25 bg-red-500/15 text-[#F8FAFC]/90 hover:bg-red-500/25 hover:shadow-[0_0_24px_rgba(220,38,38,0.15)] shadow-[0_0_16px_rgba(220,38,38,0.08)] text-xs font-bold tracking-wide transition-all duration-200"
              >
                <span className="flex items-center gap-1.5">
                  <Stamp className="size-3.5" />
                  {applying ? '用印中…' : '批 红 / 用 印'}
                </span>
              </Button>
            ) : null}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
