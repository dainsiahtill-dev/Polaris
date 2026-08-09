import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { parseJsonLikeOutputWithMeta } from './llmOutputParser';
import { parseLlmConfigMessage } from './llmEventMetaParser';
/* ── Badge primitives ─────────────────────────────────────────────────── */
function Badge({ children, className = '' }) {
    return (_jsx("span", { className: `inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wider ${className}`, children: children }));
}
function TagBadge({ tag }) {
    return _jsx(Badge, { className: "border-amber-400/30 bg-[rgba(100,70,20,0.25)] text-amber-200/90", children: tag });
}
function ProviderBadge({ provider }) {
    const short = provider.replace(/-\d{10,}$/, '');
    return _jsx(Badge, { className: "soft-chip text-slate-200", children: short });
}
function ModelBadge({ model }) {
    return _jsx(Badge, { className: "soft-chip text-slate-200", children: model });
}
function StageBadge({ stage }) {
    const styles = {
        started: 'border-blue-400/30 bg-[rgba(20,40,80,0.35)] text-blue-200',
        llm_calling: 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200',
        parsing: 'border-slate-400/30 bg-[rgba(30,30,30,0.30)] text-slate-300',
        completed: 'border-emerald-400/30 bg-[rgba(14,45,40,0.35)] text-emerald-200',
        failed: 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200',
    };
    return _jsx(Badge, { className: styles[stage] || 'border-gray-400/30 bg-[rgba(30,30,30,0.30)] text-gray-300', children: stage });
}
function LevelBadge({ level }) {
    const styles = {
        info: 'border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200',
        warn: 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200',
        error: 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200',
    };
    return _jsx(Badge, { className: styles[level || 'info'] || styles.info, children: level || 'info' });
}
function TokenBadge({ label, value }) {
    return (_jsxs("span", { className: "inline-flex items-center gap-1 rounded-md soft-chip px-2 py-0.5 text-[10px]", children: [_jsx("span", { className: "text-gray-400", children: label }), _jsx("span", { className: "font-semibold text-slate-200", children: value.toLocaleString() })] }));
}
function DurationBadge({ ms }) {
    const display = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
    return _jsx(Badge, { className: "border-gray-400/20 bg-[rgba(30,25,50,0.30)] text-gray-300", children: display });
}
function StatusDot({ ok }) {
    return (_jsx("span", { className: `size-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-400'}` }));
}
/* ── Collapsible section ──────────────────────────────────────────────── */
function Collapsible({ title, children, defaultOpen = false, className = '' }) {
    const [open, setOpen] = useState(defaultOpen);
    return (_jsxs("div", { className: className, children: [_jsxs("button", { onClick: () => setOpen(!open), className: "flex items-center gap-1.5 text-[10px] text-gray-400 hover:text-gray-200 transition-colors", children: [_jsx("span", { className: `transition-transform ${open ? 'rotate-90' : ''}`, children: "\u25B6" }), title] }), open && _jsx("div", { className: "mt-1.5 ml-3", children: children })] }));
}
function isJsonRecord(value) {
    return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function asText(value) {
    if (typeof value === 'string')
        return value.trim();
    if (typeof value === 'number' || typeof value === 'boolean')
        return String(value);
    return '';
}
function asStringList(value) {
    if (!Array.isArray(value))
        return [];
    return value
        .map((item) => {
        if (typeof item === 'string')
            return item.trim();
        if (typeof item === 'number' || typeof item === 'boolean')
            return String(item);
        return '';
    })
        .filter((item) => item.length > 0);
}
function formatKeyLabel(key) {
    const labels = {
        overall_goal: 'Overall Goal',
        focus: 'Focus',
        brief: 'Brief',
        summary: 'Summary',
        qa: 'QA',
        next: 'Next',
        reason: 'Reason',
        notes: 'Notes',
        files: 'Files',
        commands: 'Commands',
        tool_commands: 'Tool Commands',
        findings: 'Findings',
        issues: 'Issues',
        recommendations: 'Recommendations',
        constraints: 'Constraints',
        context_files: 'Context Files',
        target_files: 'Target Files',
        stop_conditions: 'Stop Conditions',
        acceptance: 'Acceptance',
    };
    return labels[key] || key.replace(/_/g, ' ');
}
function prettyJson(value) {
    try {
        return JSON.stringify(value, null, 2);
    }
    catch {
        return String(value);
    }
}
function JsonTextBlock({ title, text }) {
    if (!text)
        return null;
    return (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2", children: [_jsx("div", { className: "text-[10px] text-slate-400 mb-1", children: title }), _jsx("div", { className: "text-[11px] text-gray-200 whitespace-pre-wrap break-words", children: text })] }));
}
function JsonListBlock({ title, items }) {
    if (!items.length)
        return null;
    return (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2", children: [_jsx("div", { className: "text-[10px] text-slate-400 mb-1", children: title }), _jsx("ul", { className: "space-y-1", children: items.map((item, idx) => (_jsxs("li", { className: "text-[11px] text-gray-200 break-all", children: [_jsxs("span", { className: "text-gray-500 mr-1", children: [idx + 1, "."] }), item] }, `${title}-${idx}`))) })] }));
}
function JsonTaskCard({ task, index }) {
    if (!isJsonRecord(task)) {
        return (_jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(task) }));
    }
    const id = asText(task.id);
    const title = asText(task.title) || `Task ${index + 1}`;
    const priority = asText(task.priority);
    const goal = asText(task.goal);
    const targetFiles = asStringList(task.target_files);
    const acceptance = asStringList(task.acceptance);
    const constraints = asStringList(task.constraints);
    const contextFiles = asStringList(task.context_files);
    const backlogRef = asText(task.backlog_ref);
    return (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-1.5", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-1.5", children: [_jsx("span", { className: "text-[11px] font-semibold text-gray-100", children: title }), id && _jsx(Badge, { className: "border-gray-400/20 bg-[rgba(30,25,50,0.25)] text-gray-300", children: id }), priority && _jsxs(Badge, { className: "border-amber-400/20 bg-[rgba(80,60,10,0.25)] text-amber-200", children: ["P", priority] })] }), goal && _jsx("div", { className: "text-[11px] text-gray-200 whitespace-pre-wrap break-all", children: goal }), _jsx(JsonListBlock, { title: "Target Files", items: targetFiles }), _jsx(JsonListBlock, { title: "Acceptance", items: acceptance }), _jsx(JsonListBlock, { title: "Constraints", items: constraints }), _jsx(JsonListBlock, { title: "Context Files", items: contextFiles }), backlogRef && (_jsxs("div", { className: "text-[10px] text-gray-400 whitespace-pre-wrap break-all", children: ["backlog_ref: ", backlogRef] })), isJsonRecord(task.required_evidence) && (_jsx(Collapsible, { title: _jsx("span", { className: "text-[10px] text-gray-400", children: "required_evidence" }), children: _jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(task.required_evidence) }) })), isJsonRecord(task.policy_overrides) && (_jsx(Collapsible, { title: _jsx("span", { className: "text-[10px] text-gray-400", children: "policy_overrides" }), children: _jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(task.policy_overrides) }) }))] }));
}
function StructuredJsonOutput({ value }) {
    if (Array.isArray(value)) {
        const primitiveItems = asStringList(value);
        if (primitiveItems.length === value.length) {
            return _jsx(JsonListBlock, { title: "Output", items: primitiveItems });
        }
        return (_jsx("div", { className: "space-y-2", children: value.map((item, idx) => (_jsx(Collapsible, { title: _jsxs("span", { className: "text-[10px] text-slate-400", children: ["Item ", idx + 1] }), children: _jsx("pre", { className: "text-[10px] text-gray-200 whitespace-pre-wrap break-all max-h-52 overflow-auto scrollbar-thin", children: prettyJson(item) }) }, `json-array-item-${idx}`))) }));
    }
    if (!isJsonRecord(value)) {
        return (_jsx("pre", { className: "text-[11px] text-gray-200 whitespace-pre-wrap break-all max-h-80 overflow-auto scrollbar-thin", children: prettyJson(value) }));
    }
    const handled = new Set();
    const chips = [];
    const acceptanceTag = asText(value.acceptance);
    if (acceptanceTag) {
        chips.push({
            label: 'acceptance',
            value: acceptanceTag,
            className: acceptanceTag.toUpperCase() === 'PASS'
                ? 'border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200'
                : 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200',
        });
        handled.add('acceptance');
    }
    const riskLevel = asText(value.risk_level);
    if (riskLevel) {
        chips.push({
            label: 'risk',
            value: riskLevel,
            className: 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200',
        });
        handled.add('risk_level');
    }
    const fsmState = asText(value.fsm_state);
    if (fsmState) {
        chips.push({
            label: 'fsm',
            value: fsmState,
            className: 'border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200',
        });
        handled.add('fsm_state');
    }
    if (typeof value.need_more_context === 'boolean') {
        chips.push({
            label: 'context',
            value: value.need_more_context ? 'need_more_context' : 'context_ready',
            className: value.need_more_context
                ? 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200'
                : 'border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200',
        });
        handled.add('need_more_context');
    }
    const textKeys = ['overall_goal', 'focus', 'brief', 'summary', 'qa', 'next', 'reason', 'notes'];
    const listKeys = ['files', 'commands', 'tool_commands', 'findings', 'issues', 'recommendations', 'constraints', 'context_files', 'target_files', 'stop_conditions'];
    const textBlocks = [];
    for (const key of textKeys) {
        const text = asText(value[key]);
        if (text) {
            textBlocks.push(_jsx(JsonTextBlock, { title: formatKeyLabel(key), text: text }, `text-${key}`));
            handled.add(key);
        }
    }
    const listBlocks = [];
    for (const key of listKeys) {
        const items = asStringList(value[key]);
        if (items.length > 0) {
            listBlocks.push(_jsx(JsonListBlock, { title: formatKeyLabel(key), items: items }, `list-${key}`));
            handled.add(key);
        }
    }
    if (Array.isArray(value.acceptance)) {
        const acceptanceItems = asStringList(value.acceptance);
        if (acceptanceItems.length > 0) {
            listBlocks.push(_jsx(JsonListBlock, { title: "Acceptance", items: acceptanceItems }, "list-acceptance"));
            handled.add('acceptance');
        }
    }
    const tasks = Array.isArray(value.tasks) ? value.tasks : [];
    if (tasks.length > 0)
        handled.add('tasks');
    const toolPlan = Array.isArray(value.tool_plan) ? value.tool_plan : [];
    if (toolPlan.length > 0)
        handled.add('tool_plan');
    const plan = isJsonRecord(value.plan) ? value.plan : null;
    if (plan)
        handled.add('plan');
    const act = isJsonRecord(value.act) ? value.act : null;
    if (act)
        handled.add('act');
    const remaining = Object.entries(value).filter(([key]) => !handled.has(key));
    return (_jsxs("div", { className: "space-y-2", children: [chips.length > 0 && (_jsx("div", { className: "flex flex-wrap gap-1.5", children: chips.map((chip) => (_jsxs(Badge, { className: chip.className, children: [chip.label, ": ", chip.value] }, `${chip.label}-${chip.value}`))) })), textBlocks.length > 0 && _jsx("div", { className: "space-y-2", children: textBlocks }), listBlocks.length > 0 && _jsx("div", { className: "space-y-2", children: listBlocks }), plan && (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-2", children: [_jsx("div", { className: "text-[10px] text-slate-400", children: "Plan" }), _jsx(JsonTextBlock, { title: "Summary", text: asText(plan.summary) }), _jsx(JsonListBlock, { title: "Acceptance", items: asStringList(plan.acceptance) }), Array.isArray(plan.steps) && plan.steps.length > 0 && (_jsx("div", { className: "space-y-1.5", children: plan.steps.map((step, idx) => {
                            if (!isJsonRecord(step)) {
                                return (_jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(step) }, `plan-step-${idx}`));
                            }
                            return (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(8,12,24,0.30)] px-2 py-1.5", children: [_jsxs("div", { className: "text-[10px] text-gray-400", children: ["Step ", idx + 1] }), _jsx(JsonTextBlock, { title: "Purpose", text: asText(step.purpose) }), _jsx(JsonTextBlock, { title: "Expected", text: asText(step.expected) }), _jsx(JsonListBlock, { title: "Files", items: asStringList(step.files) }), _jsx(JsonListBlock, { title: "Checks", items: asStringList(step.checks) })] }, `plan-step-${idx}`));
                        }) }))] })), act && (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-2", children: [_jsx("div", { className: "text-[10px] text-slate-400", children: "Act" }), _jsx(JsonTextBlock, { title: "Brief", text: asText(act.brief) }), _jsx(JsonListBlock, { title: "Files", items: asStringList(act.files) }), _jsx(JsonListBlock, { title: "Commands", items: asStringList(act.commands) }), _jsx(JsonListBlock, { title: "Tool Commands", items: asStringList(act.tool_commands) })] })), tasks.length > 0 && (_jsxs("div", { className: "space-y-1.5", children: [_jsxs("div", { className: "text-[10px] text-slate-400", children: ["Tasks (", tasks.length, ")"] }), tasks.map((task, idx) => (_jsx(JsonTaskCard, { task: task, index: idx }, `task-${idx}`)))] })), toolPlan.length > 0 && (_jsxs("div", { className: "space-y-1.5", children: [_jsxs("div", { className: "text-[10px] text-slate-400", children: ["Tool Plan (", toolPlan.length, ")"] }), toolPlan.map((item, idx) => {
                        if (!isJsonRecord(item)) {
                            return (_jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(item) }, `tool-plan-${idx}`));
                        }
                        const tool = asText(item.tool) || `step_${idx + 1}`;
                        return (_jsxs("div", { className: "rounded border border-white/[0.08] bg-[rgba(10,15,30,0.25)] px-2.5 py-2", children: [_jsx("div", { className: "flex items-center gap-1.5 mb-1", children: _jsx(Badge, { className: "border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200", children: tool }) }), isJsonRecord(item.args) ? (_jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(item.args) })) : (_jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(item) }))] }, `tool-plan-${idx}`));
                    })] })), remaining.length > 0 && (_jsx(Collapsible, { title: _jsxs("span", { className: "text-[10px] text-gray-400", children: ["Other Fields (", remaining.length, ")"] }), children: _jsx("div", { className: "space-y-1.5", children: remaining.map(([key, entryValue]) => {
                        if (typeof entryValue === 'string' || typeof entryValue === 'number' || typeof entryValue === 'boolean' || entryValue === null) {
                            return (_jsxs("div", { className: "text-[11px] text-gray-300 break-all", children: [_jsxs("span", { className: "text-gray-500 mr-1", children: [key, ":"] }), String(entryValue)] }, `remaining-${key}`));
                        }
                        return (_jsx(Collapsible, { title: _jsx("span", { className: "text-[10px] text-gray-400", children: key }), children: _jsx("pre", { className: "text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin", children: prettyJson(entryValue) }) }, `remaining-collapsible-${key}`));
                    }) }) }))] }));
}
/* ── Per-event renderers ──────────────────────────────────────────────── */
function ConfigCard({ event }) {
    const parsed = parseLlmConfigMessage(event.data.message || '');
    const hasMeta = !!(parsed.provider || parsed.model || parsed.backend || parsed.modelType);
    const extraEntries = Object.entries(parsed.fields).filter(([key]) => !['provider', 'model', 'backend'].includes(key));
    return (_jsxs("div", { className: "rounded-lg soft-panel-subtle px-3 py-1.5", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2", children: [_jsx(TagBadge, { tag: event.data.tag }), parsed.provider && _jsx(ProviderBadge, { provider: parsed.provider }), parsed.modelType && (_jsxs(Badge, { className: "border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200", children: ["type: ", parsed.modelType] })), parsed.model && _jsx(ModelBadge, { model: parsed.model }), parsed.providerType && parsed.providerType !== parsed.modelType && (_jsxs(Badge, { className: "soft-chip text-slate-300", children: ["provider: ", parsed.providerType] })), parsed.backend && (_jsxs(Badge, { className: "soft-chip text-slate-300", children: ["backend: ", parsed.backend] }))] }), !hasMeta && (_jsx("div", { className: "mt-1 text-[11px] text-gray-300 whitespace-pre-wrap break-all", children: event.data.message })), hasMeta && (_jsxs("div", { className: "mt-1.5 space-y-1", children: [extraEntries.map(([key, value]) => (_jsxs("div", { className: "text-[10px] text-gray-400 break-all", children: [_jsxs("span", { className: "text-gray-500 mr-1", children: [key, ":"] }), value] }, key))), _jsx(Collapsible, { title: _jsx("span", { className: "text-gray-500", children: "Raw Config" }), children: _jsx("pre", { className: "text-[10px] text-gray-400 whitespace-pre-wrap break-all max-h-28 overflow-auto scrollbar-thin", children: event.data.message }) })] }))] }));
}
function IterationCard({ event }) {
    const d = event.data;
    return (_jsxs("div", { className: "flex flex-wrap items-center gap-2 rounded-lg soft-panel-subtle px-3 py-2", children: [_jsxs(Badge, { className: "border-amber-300/40 bg-[rgba(120,90,20,0.30)] text-amber-100 font-bold", children: ["#", d.iteration] }), _jsx(StageBadge, { stage: d.stage }), _jsx("span", { className: "text-[10px] text-gray-400", children: d.backend }), _jsx("span", { className: "ml-auto text-[10px] text-gray-500", children: d.timestamp }), typeof d.task_count === 'number' && d.stage === 'completed' && (_jsxs(Badge, { className: "border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200", children: [d.task_count, " tasks"] }))] }));
}
function LlmCallCard({ event }) {
    const d = event.data;
    return (_jsxs("div", { className: "rounded-lg soft-panel-subtle", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 px-3 py-1.5", children: [_jsx("span", { className: "size-2 rounded-full bg-blue-400" }), _jsx("span", { className: "text-[10px] font-semibold tracking-wide text-blue-300/70", children: "LLM CALL" }), _jsx(ProviderBadge, { provider: d.provider }), _jsx(ModelBadge, { model: d.model }), _jsxs(Badge, { className: "soft-chip text-slate-300", children: [d.prompt_chars.toLocaleString(), " chars"] }), _jsx("span", { className: "text-[10px] text-gray-500 ml-auto", children: event.role })] }), _jsx("div", { className: "px-3 pb-1.5", children: _jsx("div", { className: "h-1 w-full rounded-full overflow-hidden soft-inset", children: _jsx("div", { className: "h-full w-1/3 rounded-full soft-progress" }) }) })] }));
}
function LlmResultCard({ event }) {
    const d = event.data;
    const hasThinking = d.thinking && d.thinking.trim().length > 0;
    const hasOutput = (d.output && d.output.trim().length > 0)
        || (d.output_preview && d.output_preview.trim().length > 0)
        || (d.output_json !== undefined && d.output_json !== null);
    const hasError = d.error && d.error.trim().length > 0;
    const contentType = d.content_type || ((d.output_json !== undefined && d.output_json !== null)
        ? 'json'
        : ((d.output || '').trim().startsWith('{') ? 'json' : 'text'));
    const hasServerStructuredOutput = d.output_json !== undefined && d.output_json !== null;
    const [outputView, setOutputView] = useState('structured');
    let rawOutput = '';
    let displayOutput = '';
    let parsedOutput = null;
    let parseNote = '';
    if (hasOutput) {
        rawOutput = d.output || d.output_preview || '';
        if (contentType === 'json') {
            if (hasServerStructuredOutput) {
                parsedOutput = d.output_json;
            }
            else {
                const fallback = parseJsonLikeOutputWithMeta(rawOutput);
                parsedOutput = fallback.value;
                parseNote = fallback.note;
            }
            displayOutput = parsedOutput !== null ? prettyJson(parsedOutput) : rawOutput;
        }
        else {
            displayOutput = rawOutput;
        }
    }
    if (d.output_parse_error && d.output_parse_error.trim()) {
        parseNote = d.output_parse_error.trim();
    }
    const hasStructuredOutput = contentType === 'json' && parsedOutput !== null;
    const structuredSource = hasStructuredOutput ? (hasServerStructuredOutput ? 'server' : 'client-fallback') : '';
    const rawOutputForView = rawOutput || displayOutput;
    useEffect(() => {
        if (!hasStructuredOutput)
            setOutputView('raw');
    }, [hasStructuredOutput]);
    return (_jsxs("div", { className: "rounded-lg soft-panel-subtle", children: [_jsxs("div", { className: "flex flex-wrap items-center gap-2 px-3 py-2 border-b border-white/5", children: [_jsx(StatusDot, { ok: d.ok }), _jsx("span", { className: "text-[10px] font-semibold tracking-wide text-slate-300/70", children: "LLM RESULT" }), _jsx(ProviderBadge, { provider: d.provider }), _jsx(ModelBadge, { model: d.model }), _jsx(DurationBadge, { ms: d.duration_ms }), _jsxs(Badge, { className: "soft-chip text-slate-300", children: [d.output_chars.toLocaleString(), " chars"] }), _jsx(Badge, { className: "soft-chip text-slate-400", children: contentType }), structuredSource && (_jsx(Badge, { className: "soft-chip text-slate-300", children: structuredSource })), _jsx("span", { className: "text-[10px] text-gray-500 ml-auto", children: event.role })] }), _jsxs("div", { className: "flex flex-wrap items-center gap-2 px-3 py-1.5 border-b border-white/5", children: [_jsx(TokenBadge, { label: "Prompt", value: d.tokens.prompt }), _jsx(TokenBadge, { label: "Completion", value: d.tokens.completion }), _jsx(TokenBadge, { label: "Total", value: d.tokens.total }), d.estimated && _jsx("span", { className: "text-[9px] text-gray-500 italic", children: "estimated" })] }), hasError && (_jsx("div", { className: "mx-3 my-2 rounded border border-red-500/30 bg-[rgba(80,20,15,0.20)] px-3 py-2", children: _jsx("pre", { className: "text-[11px] text-red-200 whitespace-pre-wrap break-all", children: d.error }) })), hasThinking && (_jsx(Collapsible, { title: _jsxs("span", { className: "text-slate-300/80", children: ["Thinking (", d.thinking.length.toLocaleString(), " chars)"] }), className: "px-3 py-1.5 border-b border-white/5", children: _jsx("pre", { className: "text-[11px] text-gray-300 whitespace-pre-wrap break-all max-h-60 overflow-auto scrollbar-thin", children: d.thinking }) })), hasOutput && (_jsxs(Collapsible, { title: _jsxs("span", { className: "text-slate-300/80", children: ["Output (", d.output_chars.toLocaleString(), " chars)", d.truncated && _jsx("span", { className: "text-gray-500 ml-1", children: "truncated" })] }), defaultOpen: !hasThinking, className: "px-3 py-1.5", children: [hasStructuredOutput && (_jsxs("div", { className: "flex items-center gap-2 mb-2", children: [_jsx("button", { onClick: () => setOutputView('structured'), className: `rounded px-2 py-0.5 text-[10px] border ${outputView === 'structured'
                                    ? 'soft-raised text-slate-200'
                                    : 'soft-chip text-gray-400'}`, children: "Structured" }), _jsx("button", { onClick: () => setOutputView('raw'), className: `rounded px-2 py-0.5 text-[10px] border ${outputView === 'raw'
                                    ? 'soft-raised text-slate-200'
                                    : 'soft-chip text-gray-400'}`, children: "Raw" })] })), parseNote && _jsx("div", { className: "mb-2 text-[10px] text-amber-300/80 break-all", children: parseNote }), hasStructuredOutput && outputView === 'structured' ? (_jsx(StructuredJsonOutput, { value: parsedOutput })) : (_jsx("pre", { className: "text-[11px] text-gray-200 whitespace-pre-wrap break-all max-h-80 overflow-auto scrollbar-thin", children: rawOutputForView }))] }))] }));
}
function InfoCard({ event }) {
    const d = event.data;
    return (_jsxs("div", { className: "flex items-center gap-2 rounded-lg soft-panel-subtle px-3 py-1.5", children: [_jsx(LevelBadge, { level: d.level }), d.tag && _jsx(TagBadge, { tag: d.tag }), _jsx("span", { className: "text-[11px] text-gray-300 truncate", children: d.message })] }));
}
function FallbackCard({ event }) {
    return (_jsxs("div", { className: "rounded-lg soft-panel-subtle px-3 py-1.5", children: [_jsx("span", { className: "text-[10px] text-gray-500 mr-2", children: event.event }), _jsx("pre", { className: "text-[10px] text-gray-400 whitespace-pre-wrap break-all inline", children: JSON.stringify(event.data, null, 2) })] }));
}
function StreamEventCard({ event }) {
    const data = (event.data && typeof event.data === 'object' ? event.data : {});
    const eventName = String(event.event || '').trim().toLowerCase();
    const message = String(data.message || '').trim();
    const tool = String(data.tool || '').trim();
    const success = data.success;
    const status = success === undefined ? '' : (success ? 'ok' : 'failed');
    const argsPreview = (() => {
        try {
            const raw = data.args && typeof data.args === 'object' ? JSON.stringify(data.args) : '';
            return raw.length > 180 ? `${raw.slice(0, 180)}...` : raw;
        }
        catch {
            return '';
        }
    })();
    const label = eventName === 'thinking_chunk'
        ? '思考流'
        : eventName === 'content_chunk'
            ? '输出流'
            : eventName === 'tool_call'
                ? '工具调用'
                : eventName === 'tool_result'
                    ? '工具结果'
                    : eventName;
    return (_jsxs("div", { className: "rounded-lg soft-panel-subtle px-3 py-2", children: [_jsxs("div", { className: "mb-1 flex items-center gap-2", children: [_jsx(Badge, { className: "soft-chip text-slate-200", children: label }), tool && _jsx(TagBadge, { tag: tool }), status && (_jsx(Badge, { className: status === 'ok' ? 'border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200' : 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200', children: status }))] }), message && _jsx("div", { className: "text-[11px] text-gray-200 whitespace-pre-wrap break-words", children: message }), argsPreview && _jsxs("div", { className: "mt-1 text-[10px] text-gray-400 break-all", children: ["args: ", argsPreview] })] }));
}
/* ── Main export ──────────────────────────────────────────────────────── */
export function LlmEventCard({ event }) {
    switch (event.event) {
        case 'config': return _jsx(ConfigCard, { event: event });
        case 'iteration': return _jsx(IterationCard, { event: event });
        case 'llm_call': return _jsx(LlmCallCard, { event: event });
        case 'llm_result': return _jsx(LlmResultCard, { event: event });
        case 'info': return _jsx(InfoCard, { event: event });
        case 'thinking_chunk':
        case 'content_chunk':
        case 'tool_call':
        case 'tool_result':
            return _jsx(StreamEventCard, { event: event });
        default: return _jsx(FallbackCard, { event: event });
    }
}
