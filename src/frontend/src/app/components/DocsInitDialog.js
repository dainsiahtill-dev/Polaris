import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { RiAiGenerate2 } from 'react-icons/ri';
import { Landmark, ScrollText, Stamp, Brain, ChevronLeft, Loader2, X, CheckCircle2 } from 'lucide-react';
import { apiFetch } from '@/api';
import { toast } from 'sonner';
import { useNDJSONStream } from '@/hooks/useNDJSONStream';
import { normalizeDocsInitPreviewPayload, } from '@/app/utils/docsInitPreview';
import { workspaceLabel } from '@/app/utils/workspaceDisplay';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, } from '@/app/components/ui/dialog';
import { Button } from '@/app/components/ui/button';
import { ScrollArea } from '@/app/components/ui/scroll-area';
const WIZARD_MODE = 'minimal';
const INVALID_PREVIEW_ERROR = 'Plan preview data incomplete, please redraft plan.';
const SLOT_LABELS = {
    deployment_mode: '部署方式',
    auth_mode: '访问控制',
    file_size_limit: '文件规模',
    file_ops_scope: '目录与删除',
    load_test_requirement: '并发压测',
};
function splitLines(value) {
    return String(value || '')
        .replace(/\r\n/g, '\n')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean);
}
function buildStreamingThoughtPreview(raw) {
    const text = String(raw || '').trim();
    if (!text)
        return '';
    try {
        const parsed = JSON.parse(text);
        const reply = String(parsed.reply || '').trim();
        if (reply)
            return reply;
    }
    catch {
        // continue with raw preview
    }
    const compact = text.replace(/\s+/g, ' ').trim();
    if (!compact)
        return '';
    return compact.slice(-360);
}
export function DocsInitDialog({ open, onOpenChange, workspace, workspaceStatus, docsPresent, onApplied, }) {
    const [step, setStep] = useState(2);
    const [goal, setGoal] = useState('');
    const [inScope, setInScope] = useState('');
    const [outOfScope, setOutOfScope] = useState('');
    const [constraints, setConstraints] = useState('');
    const [definitionOfDone, setDefinitionOfDone] = useState('');
    const [backlog, setBacklog] = useState('');
    const [tingyiMessage, setTingyiMessage] = useState('');
    const [dialogueTurns, setDialogueTurns] = useState([]);
    const [dialogueMeta, setDialogueMeta] = useState({
        phase: 'clarifying',
        answered_slots: [],
        unresolved_slots: [],
    });
    const [tiaochenDraft, setTiaochenDraft] = useState([]);
    const [preview, setPreview] = useState(null);
    const [loadingPreview, setLoadingPreview] = useState(false);
    const [applying, setApplying] = useState(false);
    const [dialoguing, setDialoguing] = useState(false);
    const [error, setError] = useState(null);
    // Draft Plan streaming progress state
    const [previewProgress, setPreviewProgress] = useState({ open: false, stage: '', message: '', progress: 0 });
    const docsMissing = useMemo(() => {
        if (docsPresent === false)
            return true;
        return workspaceStatus?.status === 'NEEDS_DOCS_INIT';
    }, [docsPresent, workspaceStatus?.status]);
    const displayWorkspace = workspaceLabel(workspace, '');
    const applySuggestedFields = (fields) => {
        if (!fields)
            return;
        if (typeof fields.goal === 'string')
            setGoal(fields.goal);
        if (typeof fields.in_scope === 'string')
            setInScope(fields.in_scope);
        if (typeof fields.out_of_scope === 'string')
            setOutOfScope(fields.out_of_scope);
        if (typeof fields.constraints === 'string')
            setConstraints(fields.constraints);
        if (typeof fields.definition_of_done === 'string')
            setDefinitionOfDone(fields.definition_of_done);
        if (typeof fields.backlog === 'string')
            setBacklog(fields.backlog);
    };
    useEffect(() => {
        if (!open)
            return;
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
    const streamingIndexRef = useRef(-1);
    const onRawEvent = useCallback((event) => {
        if (event.type === 'reasoning_chunk') {
            const content = String(event.data.content || '');
            setDialogueTurns((prev) => {
                const idx = streamingIndexRef.current;
                if (idx < 0 || idx >= prev.length)
                    return prev;
                const copy = [...prev];
                copy[idx] = { ...copy[idx], reasoning: (copy[idx].reasoning || '') + content };
                return copy;
            });
        }
        if (event.type === 'thinking_chunk') {
            const content = String(event.data.content || '');
            if (!content)
                return;
            setDialogueTurns((prev) => {
                const idx = streamingIndexRef.current;
                if (idx < 0 || idx >= prev.length)
                    return prev;
                const copy = [...prev];
                copy[idx] = { ...copy[idx], rawContent: (copy[idx].rawContent || '') + content };
                return copy;
            });
        }
    }, []);
    // Preview 流式进度事件处理
    const onPreviewRawEvent = useCallback((event) => {
        if (event.type === 'stage') {
            const data = event.data;
            setPreviewProgress((prev) => ({
                ...prev,
                open: true,
                stage: data.stage,
                message: data.message,
                progress: data.progress,
                fields: data.fields || prev.fields,
            }));
        }
        else if (event.type === 'thinking') {
            // 实时更新thinking内容
            const data = event.data;
            setPreviewProgress((prev) => ({
                ...prev,
                thinking: data.accumulated || data.content,
            }));
        }
    }, []);
    const onPreviewComplete = useCallback((data) => {
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
    const onPreviewError = useCallback((err) => {
        setError(err || '条陈拟稿失败');
        setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
        setLoadingPreview(false);
    }, []);
    const { isStreaming: isPreviewStreaming, startStream: startPreviewStream, stopStream: stopPreviewStream } = useNDJSONStream({ onEvent: onPreviewRawEvent, onComplete: onPreviewComplete, onError: onPreviewError });
    const onComplete = useCallback((data) => {
        const reply = String(data.reply || '').trim();
        const questions = (data.questions || []).map((s) => String(s).trim()).filter(Boolean);
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
        const fields = data.fields;
        applySuggestedFields(fields);
        const meta = (data.meta || {});
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
        const tiaochen = (data.tiaochen || []).map((item) => String(item).trim()).filter(Boolean);
        if (tiaochen.length > 0) {
            setTiaochenDraft(tiaochen);
        }
        else if (fields?.backlog) {
            setTiaochenDraft(splitLines(fields.backlog));
        }
        else if (backlog) {
            setTiaochenDraft(splitLines(backlog));
        }
        setTingyiMessage('');
        setDialoguing(false);
    }, [backlog]);
    const onError = useCallback((error) => {
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
        const userTurn = { role: 'user', content: message };
        const historyPayload = [
            ...dialogueTurns.map((turn) => ({
                role: turn.role,
                content: turn.content,
                questions: turn.role === 'assistant' ? (turn.questions || []) : [],
            })),
            { role: 'user', content: message },
        ];
        setDialogueTurns((prev) => {
            const next = [...prev, userTurn, { role: 'assistant', content: '' }];
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
        }
        catch (err) {
            setError(err instanceof Error ? err.message : 'Approve write failed.');
        }
        finally {
            setApplying(false);
        }
    };
    const updatePreviewFile = (index, content) => {
        setPreview((prev) => {
            if (!prev)
                return prev;
            const nextFiles = [...prev.files];
            nextFiles[index] = { ...nextFiles[index], content };
            return { ...prev, files: nextFiles };
        });
    };
    const phaseReady = dialogueMeta.phase === 'ready_for_draft';
    return (_jsx(Dialog, { open: open, onOpenChange: onOpenChange, children: _jsxs(DialogContent, { "data-testid": "docs-init-dialog", className: "soft-panel grid h-[min(96vh,920px)] w-[98vw] max-w-[98vw] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-xl text-[#F8FAFC]", children: [_jsxs(DialogHeader, { className: "relative pb-3 border-b border-white/[0.08]", children: [_jsx("div", { className: "absolute inset-x-0 bottom-0 h-px soft-divider opacity-60" }), _jsxs("div", { className: "flex min-w-0 flex-col gap-3 pr-10 xl:flex-row xl:items-start xl:justify-between", children: [_jsx("div", { className: "min-w-0", children: _jsxs("div", { className: "flex items-center gap-3", children: [_jsx("div", { className: "soft-raised flex h-9 w-9 items-center justify-center rounded-lg", children: _jsx(Landmark, { className: "size-4.5 text-[var(--soft-accent)]" }) }), _jsxs("div", { className: "min-w-0", children: [_jsxs(DialogTitle, { className: "break-words text-lg font-bold tracking-wide", children: [_jsx("span", { className: "text-[var(--soft-accent)]", children: "\u653F \u4E8B \u5802" }), _jsx("span", { className: "ml-2.5 text-xs font-normal tracking-widest text-white/30", children: "Architect Discussion Planning" })] }), _jsx(DialogDescription, { className: "mt-0.5 text-[11px] text-[#F8FAFC]/30", children: docsMissing
                                                            ? 'Current workspace missing docs/, please start Docs Wizard discussion first.'
                                                            : 'Dialogue Q&A → Draft Plan → Approve ┃ Full process bound to Architect LLM' })] })] }) }), _jsxs("div", { className: "flex shrink-0 flex-wrap items-center gap-3 pt-1", children: [_jsxs("div", { className: "soft-chip flex items-center gap-2 rounded-lg px-3 py-1.5", children: [_jsx("div", { className: `h-1.5 w-1.5 rounded-full ${phaseReady ? 'bg-[#22C55E]' : 'bg-[var(--soft-accent)] animate-pulse'}` }), _jsx("span", { className: "text-[10px] tracking-widest text-white/60", children: phaseReady ? 'Discussion ready · Can draft plan' : 'Discussion in progress' })] }), _jsxs("span", { className: "text-[10px] text-white/25 font-mono", children: [step === 3 ? 'II' : 'I', " / II"] })] })] }), workspace ? (_jsxs("div", { "data-testid": "docs-init-workspace-label", className: "mt-2 truncate pr-10 font-mono text-[10px] tracking-wider text-white/25", title: workspace, children: ["Workspace \u25B8 ", displayWorkspace] })) : null] }), _jsxs("div", { "data-testid": "docs-init-body", className: "min-h-0 overflow-hidden", children: [error ? (_jsxs("div", { className: "soft-raised mb-3 break-words rounded-lg border-red-500/30 bg-red-500/10 px-4 py-2.5 text-xs text-red-200", children: [_jsx("span", { className: "mr-2 text-red-400", children: "!" }), error] })) : null, previewProgress.open && (_jsx("div", { className: "fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm", children: _jsxs("div", { className: "soft-panel w-full max-w-md rounded-xl p-6", children: [_jsxs("div", { className: "flex items-center gap-3 mb-4", children: [_jsxs("div", { className: "relative", children: [_jsx("div", { className: "h-10 w-10 rounded-full border-2 border-white/10 border-t-[var(--soft-accent)] animate-spin" }), _jsx(ScrollText, { className: "absolute inset-0 m-auto h-4 w-4 text-[var(--soft-accent)]" })] }), _jsxs("div", { children: [_jsx("h3", { className: "text-sm font-bold text-[var(--soft-accent)]", children: "Drafting Plan..." }), _jsx("p", { className: "text-[11px] text-white/50", children: "Architect is generating documents for you" })] })] }), _jsxs("div", { className: "mb-4", children: [_jsx("div", { className: "h-1.5 w-full rounded-full bg-white/10 overflow-hidden", children: _jsx("div", { className: "soft-progress h-full rounded-full transition-all duration-500", style: { width: `${previewProgress.progress}%` } }) }), _jsxs("div", { className: "mt-1 flex justify-between text-[10px] text-white/40", children: [_jsxs("span", { children: [previewProgress.progress, "%"] }), _jsx("span", { children: previewProgress.stage === 'llm_start' ? 'AI思考中' : '处理中' })] })] }), _jsxs("div", { className: "soft-inset mb-4 rounded-lg p-3", children: [_jsxs("div", { className: "flex min-w-0 items-center gap-2 text-xs text-white/70", children: [_jsx(Loader2, { className: "h-3.5 w-3.5 animate-spin text-[var(--soft-accent)]" }), _jsx("span", { className: "min-w-0 break-words", children: previewProgress.message })] }), "              "] }), previewProgress.thinking ? (_jsxs("div", { className: "soft-inset mb-4 max-h-48 overflow-y-auto rounded-lg p-3", children: [_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx(Brain, { className: "h-3 w-3 text-[var(--soft-accent)]" }), _jsx("span", { className: "text-[10px] font-bold text-[var(--soft-accent)]/60", children: "Architect thinking..." })] }), _jsxs("div", { className: "break-words whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-white/50", children: [previewProgress.thinking.slice(-800), previewProgress.thinking.length > 800 && (_jsx("span", { className: "text-white/20", children: "... (\u524D\u9762\u5185\u5BB9\u5DF2\u7701\u7565)" })), _jsx("span", { className: "inline-block w-[2px] h-[1em] bg-[var(--soft-accent)] align-middle animate-pulse ml-0.5" })] })] })) : null, previewProgress.fields && Object.keys(previewProgress.fields).length > 0 && (_jsxs("div", { className: "soft-inset mb-4 max-h-32 overflow-y-auto rounded-lg p-3 border-[#22C55E]/20", children: [_jsx("div", { className: "text-[10px] font-bold text-[#22C55E]/60 mb-2", children: "\u5DF2\u751F\u6210\u5185\u5BB9" }), _jsx("div", { className: "space-y-1", children: Object.entries(previewProgress.fields).map(([key, values]) => (values && values.length > 0 ? (_jsxs("div", { className: "flex items-start gap-2 text-[10px]", children: [_jsx(CheckCircle2, { className: "h-3 w-3 text-[#22C55E] mt-0.5 shrink-0" }), _jsxs("span", { className: "min-w-0 break-words text-white/60", children: [SLOT_LABELS[key] || key, ": ", _jsxs("span", { className: "text-white/80", children: [values.length, " \u9879"] })] })] }, key)) : null)) })] })), _jsxs(Button, { variant: "secondary", onClick: () => {
                                            stopPreviewStream();
                                            setPreviewProgress({ open: false, stage: '', message: '', progress: 0 });
                                            setLoadingPreview(false);
                                        }, className: "soft-chip w-full cursor-pointer text-white/70 hover:bg-white/[0.08] hover:text-white", children: [_jsx(X, { className: "mr-1.5 h-3.5 w-3.5" }), "Cancel Draft"] })] }) })), step === 2 ? (_jsxs("div", { "data-testid": "docs-init-dialogue-step", className: "grid h-full min-h-0 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-[1.1fr_1.9fr]", children: [_jsx(ScrollArea, { "data-testid": "docs-init-input-scroll", className: "soft-panel-subtle h-full min-h-0 rounded-xl", children: _jsxs("div", { className: "grid gap-4 p-5 text-sm", children: [_jsxs("div", { className: "soft-raised rounded-lg px-4 py-2.5 text-[11px] leading-relaxed text-white/60", children: [_jsx("span", { className: "text-[var(--soft-accent)] font-semibold", children: "Tip" }), _jsx("span", { className: "mx-1.5 text-white/20", children: "\u2502" }), "Enter goal and have 1-3 dialogue rounds, then click Draft Plan to generate doc draft."] }), _jsxs("label", { className: "grid gap-1.5", children: [_jsx("span", { className: "text-xs font-semibold tracking-wide text-[var(--soft-accent)]/70", children: "Project Goal" }), _jsx("input", { "data-testid": "docs-init-goal-input", value: goal, onChange: (event) => setGoal(event.target.value), className: "soft-inset rounded-lg px-3.5 py-2.5 text-sm text-white/90 placeholder:text-white/20 focus:border-[var(--soft-accent)]/30 focus:outline-none focus:ring-1 focus:ring-[var(--soft-accent)]/15 transition-colors duration-200", placeholder: "\u4F8B\uFF1A\u505A\u4E00\u4E2A\u7B80\u5355\u7684\u6587\u4EF6\u670D\u52A1\u5668\uFF08Node.js + TypeScript\uFF09" })] }), _jsxs("label", { className: "grid gap-1.5", children: [_jsxs("span", { className: "text-xs font-semibold tracking-wide text-[var(--soft-accent)]/70", children: ["\u8865 \u5145 \u8BF4 \u660E", _jsx("span", { className: "ml-1 text-[10px] font-normal text-white/25", children: "\uFF08\u53EF\u9009\uFF09" })] }), _jsx("textarea", { "data-testid": "docs-init-message-input", value: tingyiMessage, onChange: (event) => setTingyiMessage(event.target.value), className: "soft-inset min-h-[84px] rounded-lg px-3.5 py-2.5 text-sm text-white/90 placeholder:text-white/20 focus:border-[var(--soft-accent)]/30 focus:outline-none focus:ring-1 focus:ring-[var(--soft-accent)]/15 resize-none transition-colors duration-200", placeholder: "Can directly answer Architect follow-up\uFF0C\u4F8B\u5982\uFF1A1 \u672C\u673A\u8FDB\u7A0B 2 \u65E0\u9274\u6743 3 \u4E0D\u9650\u5927\u5C0F 4 \u9700\u8981 5 \u4E0D\u538B\u6D4B" })] }), _jsx("div", { className: "flex flex-wrap gap-2", children: _jsx(Button, { type: "button", variant: "secondary", "data-testid": "docs-init-run-dialogue", disabled: dialoguing, onClick: runDialogue, className: "cursor-pointer soft-raised text-white/90 hover:bg-white/[0.08] transition-all duration-200", children: _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(RiAiGenerate2, { className: "size-4" }), dialoguing ? 'Dialoguing...' : '发 起 奏 对'] }) }) }), _jsxs("div", { className: "soft-raised rounded-lg px-4 py-3", children: [_jsxs("div", { className: "flex items-center gap-2 text-xs font-semibold text-white/70", children: [_jsx("div", { className: `h-2 w-2 rounded-full ${phaseReady ? 'bg-[#22C55E]' : 'bg-[var(--soft-accent)] animate-pulse'}` }), "\u5EF7 \u8BAE \u72B6 \u6001"] }), _jsxs("div", { className: "mt-2 text-[11px] text-white/40", children: ["\u9636\u6BB5\uFF1A", _jsx("span", { "data-testid": "docs-init-phase-status", className: phaseReady ? 'text-[#22C55E]' : 'text-[var(--soft-accent)]', children: phaseReady ? 'Can draft plan' : 'Filling key info' })] }), _jsxs("div", { "data-testid": "docs-init-unresolved-list", className: "mt-2.5", children: [_jsx("div", { className: "text-[10px] tracking-wide text-white/30 mb-1.5", children: "\u5F85\u8865\u5145\u4E8B\u9879" }), _jsx("div", { className: "flex flex-wrap gap-1.5", children: dialogueMeta.unresolved_slots.length === 0 ? (_jsx("span", { className: "soft-chip rounded-md px-2.5 py-1 text-[10px] font-semibold text-[#22C55E]", children: "\u2713 Ready" })) : (dialogueMeta.unresolved_slots.map((slotId) => (_jsx("span", { className: "soft-chip rounded-md px-2.5 py-1 text-[10px] text-[var(--soft-accent)]/70", children: SLOT_LABELS[slotId] || slotId }, slotId)))) })] })] }), _jsxs("details", { className: "soft-inset group rounded-lg px-4 py-3", children: [_jsxs("summary", { className: "cursor-pointer text-xs font-semibold text-white/50 hover:text-white/80 transition-colors duration-200 select-none", children: ["\u9AD8 \u7EA7 \u6761 \u4EE4", _jsx("span", { className: "ml-1 text-[10px] font-normal text-white/20", children: "\uFF08Optional\uFF09" })] }), _jsxs("div", { className: "mt-4 grid gap-3", children: [[
                                                                ['In Scope', inScope, setInScope, '每行一项'],
                                                                ['Out of Scope', outOfScope, setOutOfScope, '每行一项'],
                                                                ['Constraints', constraints, setConstraints, '建议 3-5 行'],
                                                                ['Definition of Done', definitionOfDone, setDefinitionOfDone, '勘验命令或验收条令'],
                                                            ].map(([label, value, setter, ph]) => (_jsxs("label", { className: "grid gap-1.5", children: [_jsx("span", { className: "text-[11px] text-white/40", children: label }), _jsx("textarea", { value: value, onChange: (event) => setter(event.target.value), className: "soft-inset min-h-[64px] rounded-lg px-3 py-2 text-xs text-white/80 placeholder:text-white/[0.15] focus:border-[var(--soft-accent)]/25 focus:outline-none resize-none transition-colors duration-200", placeholder: ph })] }, label))), _jsxs("label", { className: "grid gap-1.5", children: [_jsx("span", { className: "text-[11px] text-white/40", children: "Plan Draft (can override)" }), _jsx("textarea", { value: backlog, onChange: (event) => {
                                                                            setBacklog(event.target.value);
                                                                            setTiaochenDraft(splitLines(event.target.value));
                                                                        }, className: "soft-inset min-h-[64px] rounded-lg px-3 py-2 text-xs text-white/80 placeholder:text-white/[0.15] focus:border-[var(--soft-accent)]/25 focus:outline-none resize-none transition-colors duration-200", placeholder: "One task per line" })] })] })] })] }) }), _jsxs("div", { "data-testid": "docs-init-dialogue-right", className: "grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-3 overflow-hidden", children: [_jsxs("div", { "data-testid": "docs-init-dialogue-record", className: "soft-panel-subtle flex min-h-0 flex-col overflow-hidden rounded-xl p-4", children: [_jsxs("div", { className: "flex items-center justify-between mb-3 flex-shrink-0", children: [_jsxs("div", { className: "flex items-center gap-2.5", children: [_jsx("div", { className: "h-5 w-0.5 rounded-full bg-[var(--soft-accent)]/30" }), _jsx("span", { className: "text-xs font-bold tracking-widest text-[var(--soft-accent)]/60", children: "\u594F \u5BF9 \u8BB0 \u5F55" })] }), _jsxs("span", { className: "text-[10px] text-white/25 font-mono", children: [dialogueTurns.length, " \u8F6E"] })] }), _jsx(ScrollArea, { className: "flex-1", style: { minHeight: '150px' }, children: _jsx("div", { className: "grid gap-3 pr-2", children: dialogueTurns.length === 0 ? (_jsxs("div", { className: "flex flex-col items-center justify-center py-12 text-center", children: [_jsx(Landmark, { className: "size-8 mb-3 text-white/[0.15]" }), _jsx("div", { className: "text-xs text-white/25", children: "Discussion not started" }), _jsx("div", { className: "text-[10px] text-white/[0.12] mt-1", children: "Enter project goal, click Start Dialogue to start discussion" })] })) : (dialogueTurns.map((turn, index) => {
                                                            const isUser = turn.role === 'user';
                                                            const isStreamingTurn = turn.role === 'assistant' && index === streamingIndexRef.current && dialoguing;
                                                            return (_jsxs("div", { className: `rounded-xl border px-4 py-3 text-xs transition-all duration-200 ${isUser
                                                                    ? 'soft-raised ml-8'
                                                                    : 'soft-inset mr-8'}`, children: [_jsx("div", { className: `mb-1.5 ${isUser ? 'text-right' : 'text-left'}`, children: _jsx("span", { className: `inline-flex items-center gap-1 soft-chip rounded-md px-2 py-0.5 text-[9px] font-bold tracking-widest ${isUser
                                                                                ? 'text-[var(--soft-accent)]'
                                                                                : 'text-white/60'}`, children: isUser ? 'User' : 'Architect' }) }), _jsx("div", { className: "break-words whitespace-pre-wrap leading-relaxed", children: isStreamingTurn ? (_jsxs("div", { className: "space-y-2", children: [turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || '') ? (_jsxs("div", { className: "soft-inset rounded-lg px-3 py-2", children: [_jsxs("div", { className: "flex items-center gap-1.5 mb-1", children: [_jsx(Brain, { className: "size-3 text-[var(--soft-accent)]/60" }), _jsx("span", { className: "text-[9px] font-bold tracking-widest text-[var(--soft-accent)]/60", children: "\u81EA \u8A00 \u81EA \u8BED" })] }), _jsxs("div", { className: "max-h-[140px] overflow-y-auto break-words text-[11px] italic leading-relaxed text-white/50", children: [turn.reasoning || buildStreamingThoughtPreview(turn.rawContent || ''), _jsx("span", { className: "inline-block w-[2px] h-[1em] bg-[var(--soft-accent)] align-middle animate-pulse ml-0.5" })] })] })) : null, turn.rawContent ? (_jsxs("div", { className: "soft-inset rounded-lg px-3 py-2", children: [_jsx("div", { className: "text-[9px] font-bold tracking-widest text-white/40 mb-0.5", children: "Receiving response..." }), _jsxs("div", { className: "break-words text-[10px] italic text-white/30", children: ["Received ", turn.rawContent.length, " chars"] })] })) : null, _jsxs("span", { className: "text-white/40 text-[10px] flex items-center gap-2", children: [_jsxs("span", { className: "inline-flex gap-0.5", children: [_jsx("span", { className: "inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce", style: { animationDelay: '0ms' } }), _jsx("span", { className: "inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce", style: { animationDelay: '150ms' } }), _jsx("span", { className: "inline-block w-1 h-1 rounded-full bg-[var(--soft-accent)]/60 animate-bounce", style: { animationDelay: '300ms' } })] }), turn.reasoning
                                                                                            ? 'Architect drafting response...'
                                                                                            : buildStreamingThoughtPreview(turn.rawContent || '')
                                                                                                ? 'Real-time streaming content'
                                                                                                : turn.rawContent
                                                                                                    ? 'Response streaming...'
                                                                                                    : 'Architect thinking...'] })] })) : (_jsxs(_Fragment, { children: [turn.reasoning ? (_jsxs("details", { className: "mb-2", children: [_jsx("summary", { className: "text-[9px] tracking-widest text-[var(--soft-accent)]/35 cursor-pointer hover:text-[var(--soft-accent)]/60 transition-colors duration-200 select-none", children: "Expand reasoning chain" }), _jsx("div", { className: "mt-1.5 max-h-[140px] overflow-y-auto break-words soft-inset rounded-lg px-3 py-2 text-[11px] italic leading-relaxed text-white/45", children: turn.reasoning })] })) : null, _jsx("div", { className: isUser ? 'break-words text-white/85' : 'break-words text-white/80', children: turn.content || (() => {
                                                                                        if (!turn.rawContent)
                                                                                            return 'Plan updated based on discussion.';
                                                                                        try {
                                                                                            const parsed = JSON.parse(turn.rawContent);
                                                                                            return parsed.reply || turn.rawContent;
                                                                                        }
                                                                                        catch {
                                                                                            return turn.rawContent;
                                                                                        }
                                                                                    })() })] })) }), turn.questions && turn.questions.length > 0 ? (_jsx("div", { className: "mt-2.5 border-t border-white/[0.08] pt-2 text-[11px] text-white/60 space-y-0.5", children: turn.questions.map((question, qIndex) => (_jsxs("div", { className: "flex min-w-0 gap-1.5", children: [_jsxs("span", { className: "text-[var(--soft-accent)]/35 font-mono", children: [qIndex + 1, "."] }), _jsx("span", { className: "min-w-0 break-words", children: question })] }, `${index}-${qIndex}`))) })) : null] }, `${turn.role}-${index}`));
                                                        })) }) })] }), _jsxs("div", { "data-testid": "docs-init-plan-draft", className: "soft-raised max-h-[22vh] overflow-hidden rounded-xl p-4 border-[#22C55E]/20", children: [_jsxs("div", { className: "flex items-center gap-2.5 mb-2", children: [_jsx("div", { className: "h-4 w-0.5 rounded-full bg-[#22C55E]/30" }), _jsx("span", { className: "text-xs font-bold tracking-widest text-[#22C55E]/60", children: "Plan \u00B7 Draft" }), _jsx("span", { className: "text-[9px] text-[#22C55E]/25 tracking-wide", children: "Draft" })] }), _jsx("div", { className: "grid gap-0.5 text-xs text-white/60 max-h-[18vh] overflow-y-auto pr-1", children: tiaochenDraft.length === 0 ? (_jsx("div", { className: "text-white/20 text-[11px] py-2", children: "Plan not yet generated, please start dialogue first." })) : (tiaochenDraft.map((item, index) => (_jsxs("div", { className: "flex gap-2 py-0.5 rounded px-2 hover:bg-white/[0.04] transition-colors duration-150", children: [_jsx("span", { className: "text-[#22C55E]/35 font-mono text-[10px] w-4 text-right flex-shrink-0", children: index + 1 }), _jsx("span", { className: "min-w-0 break-words", children: item })] }, `${index}-${item}`)))) })] })] })] })) : null, step === 3 ? (_jsxs("div", { "data-testid": "docs-init-approve-step", className: "grid h-full min-h-0 grid-rows-[auto_auto_minmax(0,1fr)] gap-3 overflow-hidden", children: [_jsxs("div", { className: "soft-raised flex min-w-0 items-center gap-3 rounded-lg px-4 py-3 text-xs text-white/60", children: [_jsx(ScrollText, { className: "size-5 text-[var(--soft-accent)]/70 flex-shrink-0" }), _jsxs("div", { className: "min-w-0", children: [_jsxs("div", { className: "break-all text-white/70", children: ["Plan doc directory\uFF1A", _jsx("span", { className: "text-[var(--soft-accent)] font-semibold", children: preview?.target_root || 'docs' })] }), _jsx("div", { className: "text-white/30 text-[10px] mt-0.5", children: "Click Approve after confirmation to finalize docs." })] })] }), tiaochenDraft.length > 0 ? (_jsxs("div", { className: "soft-raised rounded-lg px-4 py-3 text-xs text-white/60 border-[#22C55E]/15", children: [_jsx("div", { className: "mb-2 font-bold tracking-widest text-[#22C55E]/60 text-[11px]", children: "Plan \u00B7 Draft" }), _jsx("div", { className: "space-y-0.5", children: tiaochenDraft.map((item, index) => (_jsxs("div", { className: "flex min-w-0 gap-2", children: [_jsxs("span", { className: "text-[#22C55E]/35 font-mono text-[10px]", children: [index + 1, "."] }), _jsx("span", { className: "min-w-0 break-words", children: item })] }, `${index}-${item}`))) })] })) : null, _jsx(ScrollArea, { "data-testid": "docs-init-preview-scroll", className: "soft-panel-subtle h-full min-h-0 rounded-xl", children: _jsx("div", { className: "grid gap-4 p-4", children: preview?.files.map((file, index) => (_jsxs("div", { className: "soft-inset min-w-0 overflow-hidden rounded-lg", children: [_jsxs("div", { className: "flex min-w-0 flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] bg-white/[0.02] px-4 py-2 text-[11px]", children: [_jsx("span", { className: "min-w-0 break-all font-mono text-[var(--soft-accent)]/50", children: file.path }), file.exists ? (_jsx("span", { className: "soft-chip rounded px-2 py-0.5 text-[9px] text-[var(--soft-accent)]", children: "\u5DF2\u5B58\u5728" })) : (_jsx("span", { className: "soft-chip rounded px-2 py-0.5 text-[9px] text-[#22C55E]", children: "\u65B0\u5EFA" }))] }), _jsx("textarea", { value: file.content, onChange: (event) => updatePreviewFile(index, event.target.value), className: "min-h-[180px] w-full resize-y bg-transparent px-4 py-3 font-mono text-xs leading-relaxed text-white/70 outline-none" })] }, file.path))) }) })] })) : null] }), _jsxs(DialogFooter, { "data-testid": "docs-init-footer", className: "flex flex-row flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3", children: [_jsx("div", { className: "flex flex-wrap gap-2", children: step === 3 ? (_jsx(Button, { variant: "secondary", onClick: () => setStep(step - 1), className: "cursor-pointer soft-chip text-white/60 hover:bg-white/[0.06] hover:text-white/80 text-xs transition-colors duration-200", children: _jsxs("span", { className: "flex items-center gap-1", children: [_jsx(ChevronLeft, { className: "size-3.5" }), "\u4E0A\u4E00\u6B65"] }) })) : null }), _jsxs("div", { className: "flex flex-wrap justify-end gap-2", children: [_jsx(Button, { variant: "ghost", onClick: () => onOpenChange(false), className: "cursor-pointer text-white/30 hover:text-white/60 text-xs transition-colors duration-200", children: "Close" }), step === 2 ? (_jsx(Button, { "data-testid": "docs-init-build-preview", onClick: buildPreview, disabled: loadingPreview, className: "cursor-pointer soft-raised text-white/90 text-xs font-bold tracking-wide transition-all duration-200", children: _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(ScrollText, { className: "size-3.5" }), loadingPreview ? 'Drafting...' : '拟 定 条 陈'] }) })) : null, step === 3 ? (_jsx(Button, { "data-testid": "docs-init-apply", onClick: applyDocs, disabled: applying, className: "cursor-pointer soft-raised border-red-500/20 text-white/90 text-xs font-bold tracking-wide transition-all duration-200", children: _jsxs("span", { className: "flex items-center gap-1.5", children: [_jsx(Stamp, { className: "size-3.5" }), applying ? 'Approving...' : 'Approve'] }) })) : null] })] })] }) }));
}
