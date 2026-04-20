import { useEffect, useState } from 'react';
import type { LlmEvent } from './LlmEventTypes';
import { parseJsonLikeOutputWithMeta } from './llmOutputParser';
import { parseLlmConfigMessage } from './llmEventMetaParser';

/* ── Badge primitives ─────────────────────────────────────────────────── */

function Badge({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wider ${className}`}>
      {children}
    </span>
  );
}

function TagBadge({ tag }: { tag: string }) {
  return <Badge className="border-amber-400/30 bg-[rgba(100,70,20,0.25)] text-amber-200/90">{tag}</Badge>;
}

function ProviderBadge({ provider }: { provider: string }) {
  const short = provider.replace(/-\d{10,}$/, '');
  return <Badge className="border-cyan-400/30 bg-[rgba(20,50,60,0.35)] text-cyan-200">{short}</Badge>;
}

function ModelBadge({ model }: { model: string }) {
  return <Badge className="border-purple-400/30 bg-[rgba(35,20,55,0.35)] text-purple-200">{model}</Badge>;
}

function StageBadge({ stage }: { stage: string }) {
  const styles: Record<string, string> = {
    started: 'border-blue-400/30 bg-[rgba(20,40,80,0.35)] text-blue-200',
    llm_calling: 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200',
    parsing: 'border-cyan-400/30 bg-[rgba(20,50,60,0.30)] text-cyan-200',
    completed: 'border-emerald-400/30 bg-[rgba(14,45,40,0.35)] text-emerald-200',
    failed: 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200',
  };
  return <Badge className={styles[stage] || 'border-gray-400/30 bg-[rgba(30,30,30,0.30)] text-gray-300'}>{stage}</Badge>;
}

function LevelBadge({ level }: { level?: string }) {
  const styles: Record<string, string> = {
    info: 'border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200',
    warn: 'border-amber-400/30 bg-[rgba(80,60,10,0.30)] text-amber-200',
    error: 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200',
  };
  return <Badge className={styles[level || 'info'] || styles.info}>{level || 'info'}</Badge>;
}

function TokenBadge({ label, value }: { label: string; value: number }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-cyan-400/15 bg-[linear-gradient(135deg,rgba(20,50,60,0.25),rgba(35,20,55,0.20))] px-2 py-0.5 text-[10px]">
      <span className="text-gray-400">{label}</span>
      <span className="font-semibold text-transparent bg-clip-text bg-gradient-to-r from-cyan-300 via-purple-300 to-pink-300">
        {value.toLocaleString()}
      </span>
    </span>
  );
}

function DurationBadge({ ms }: { ms: number }) {
  const display = ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
  return <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.30)] text-gray-300">{display}</Badge>;
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`size-2 rounded-full ${ok ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.5)]' : 'bg-red-400 shadow-[0_0_6px_rgba(248,113,113,0.5)]'}`}
    />
  );
}

/* ── Collapsible section ──────────────────────────────────────────────── */

function Collapsible({ title, children, defaultOpen = false, className = '' }: {
  title: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={className}>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-[10px] text-gray-400 hover:text-gray-200 transition-colors">
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>&#9654;</span>
        {title}
      </button>
      {open && <div className="mt-1.5 ml-3">{children}</div>}
    </div>
  );
}

/* ── JSON output parsing/rendering ────────────────────────────────────── */

type JsonRecord = Record<string, unknown>;

function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim();
      if (typeof item === 'number' || typeof item === 'boolean') return String(item);
      return '';
    })
    .filter((item) => item.length > 0);
}

function formatKeyLabel(key: string): string {
  const labels: Record<string, string> = {
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

function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function JsonTextBlock({ title, text }: { title: string; text: string }) {
  if (!text) return null;
  return (
    <div className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2">
      <div className="text-[10px] text-cyan-300/80 mb-1">{title}</div>
      <div className="text-[11px] text-gray-200 whitespace-pre-wrap break-words">{text}</div>
    </div>
  );
}

function JsonListBlock({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2">
      <div className="text-[10px] text-cyan-300/80 mb-1">{title}</div>
      <ul className="space-y-1">
        {items.map((item, idx) => (
          <li key={`${title}-${idx}`} className="text-[11px] text-gray-200 break-all">
            <span className="text-gray-500 mr-1">{idx + 1}.</span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

function JsonTaskCard({ task, index }: { task: unknown; index: number }) {
  if (!isJsonRecord(task)) {
    return (
      <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
        {prettyJson(task)}
      </pre>
    );
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

  return (
    <div className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] font-semibold text-gray-100">{title}</span>
        {id && <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.25)] text-gray-300">{id}</Badge>}
        {priority && <Badge className="border-amber-400/20 bg-[rgba(80,60,10,0.25)] text-amber-200">P{priority}</Badge>}
      </div>
      {goal && <div className="text-[11px] text-gray-200 whitespace-pre-wrap break-all">{goal}</div>}
      <JsonListBlock title="Target Files" items={targetFiles} />
      <JsonListBlock title="Acceptance" items={acceptance} />
      <JsonListBlock title="Constraints" items={constraints} />
      <JsonListBlock title="Context Files" items={contextFiles} />
      {backlogRef && (
        <div className="text-[10px] text-gray-400 whitespace-pre-wrap break-all">
          backlog_ref: {backlogRef}
        </div>
      )}
      {isJsonRecord(task.required_evidence) && (
        <Collapsible title={<span className="text-[10px] text-gray-400">required_evidence</span>}>
          <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
            {prettyJson(task.required_evidence)}
          </pre>
        </Collapsible>
      )}
      {isJsonRecord(task.policy_overrides) && (
        <Collapsible title={<span className="text-[10px] text-gray-400">policy_overrides</span>}>
          <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
            {prettyJson(task.policy_overrides)}
          </pre>
        </Collapsible>
      )}
    </div>
  );
}

function StructuredJsonOutput({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    const primitiveItems = asStringList(value);
    if (primitiveItems.length === value.length) {
      return <JsonListBlock title="Output" items={primitiveItems} />;
    }
    return (
      <div className="space-y-2">
        {value.map((item, idx) => (
          <Collapsible key={`json-array-item-${idx}`} title={<span className="text-[10px] text-cyan-300/80">Item {idx + 1}</span>}>
            <pre className="text-[10px] text-gray-200 whitespace-pre-wrap break-all max-h-52 overflow-auto scrollbar-thin">
              {prettyJson(item)}
            </pre>
          </Collapsible>
        ))}
      </div>
    );
  }

  if (!isJsonRecord(value)) {
    return (
      <pre className="text-[11px] text-gray-200 whitespace-pre-wrap break-all max-h-80 overflow-auto scrollbar-thin">
        {prettyJson(value)}
      </pre>
    );
  }

  const handled = new Set<string>();
  const chips: Array<{ label: string; value: string; className: string }> = [];

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

  const textKeys = ['overall_goal', 'focus', 'brief', 'summary', 'qa', 'next', 'reason', 'notes'] as const;
  const listKeys = ['files', 'commands', 'tool_commands', 'findings', 'issues', 'recommendations', 'constraints', 'context_files', 'target_files', 'stop_conditions'] as const;

  const textBlocks: React.ReactNode[] = [];
  for (const key of textKeys) {
    const text = asText(value[key]);
    if (text) {
      textBlocks.push(<JsonTextBlock key={`text-${key}`} title={formatKeyLabel(key)} text={text} />);
      handled.add(key);
    }
  }

  const listBlocks: React.ReactNode[] = [];
  for (const key of listKeys) {
    const items = asStringList(value[key]);
    if (items.length > 0) {
      listBlocks.push(<JsonListBlock key={`list-${key}`} title={formatKeyLabel(key)} items={items} />);
      handled.add(key);
    }
  }

  if (Array.isArray(value.acceptance)) {
    const acceptanceItems = asStringList(value.acceptance);
    if (acceptanceItems.length > 0) {
      listBlocks.push(<JsonListBlock key="list-acceptance" title="Acceptance" items={acceptanceItems} />);
      handled.add('acceptance');
    }
  }

  const tasks = Array.isArray(value.tasks) ? value.tasks : [];
  if (tasks.length > 0) handled.add('tasks');

  const toolPlan = Array.isArray(value.tool_plan) ? value.tool_plan : [];
  if (toolPlan.length > 0) handled.add('tool_plan');

  const plan = isJsonRecord(value.plan) ? value.plan : null;
  if (plan) handled.add('plan');

  const act = isJsonRecord(value.act) ? value.act : null;
  if (act) handled.add('act');

  const remaining = Object.entries(value).filter(([key]) => !handled.has(key));

  return (
    <div className="space-y-2">
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {chips.map((chip) => (
            <Badge key={`${chip.label}-${chip.value}`} className={chip.className}>
              {chip.label}: {chip.value}
            </Badge>
          ))}
        </div>
      )}

      {textBlocks.length > 0 && <div className="space-y-2">{textBlocks}</div>}
      {listBlocks.length > 0 && <div className="space-y-2">{listBlocks}</div>}

      {plan && (
        <div className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-2">
          <div className="text-[10px] text-cyan-300/80">Plan</div>
          <JsonTextBlock title="Summary" text={asText(plan.summary)} />
          <JsonListBlock title="Acceptance" items={asStringList(plan.acceptance)} />
          {Array.isArray(plan.steps) && plan.steps.length > 0 && (
            <div className="space-y-1.5">
              {plan.steps.map((step, idx) => {
                if (!isJsonRecord(step)) {
                  return (
                    <pre key={`plan-step-${idx}`} className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
                      {prettyJson(step)}
                    </pre>
                  );
                }
                return (
                  <div key={`plan-step-${idx}`} className="rounded border border-white/8 bg-[rgba(8,12,24,0.30)] px-2 py-1.5">
                    <div className="text-[10px] text-gray-400">Step {idx + 1}</div>
                    <JsonTextBlock title="Purpose" text={asText(step.purpose)} />
                    <JsonTextBlock title="Expected" text={asText(step.expected)} />
                    <JsonListBlock title="Files" items={asStringList(step.files)} />
                    <JsonListBlock title="Checks" items={asStringList(step.checks)} />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {act && (
        <div className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2 space-y-2">
          <div className="text-[10px] text-cyan-300/80">Act</div>
          <JsonTextBlock title="Brief" text={asText(act.brief)} />
          <JsonListBlock title="Files" items={asStringList(act.files)} />
          <JsonListBlock title="Commands" items={asStringList(act.commands)} />
          <JsonListBlock title="Tool Commands" items={asStringList(act.tool_commands)} />
        </div>
      )}

      {tasks.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] text-cyan-300/80">Tasks ({tasks.length})</div>
          {tasks.map((task, idx) => (
            <JsonTaskCard key={`task-${idx}`} task={task} index={idx} />
          ))}
        </div>
      )}

      {toolPlan.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[10px] text-cyan-300/80">Tool Plan ({toolPlan.length})</div>
          {toolPlan.map((item, idx) => {
            if (!isJsonRecord(item)) {
              return (
                <pre key={`tool-plan-${idx}`} className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
                  {prettyJson(item)}
                </pre>
              );
            }
            const tool = asText(item.tool) || `step_${idx + 1}`;
            return (
              <div key={`tool-plan-${idx}`} className="rounded border border-white/8 bg-[rgba(10,15,30,0.25)] px-2.5 py-2">
                <div className="flex items-center gap-1.5 mb-1">
                  <Badge className="border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200">{tool}</Badge>
                </div>
                {isJsonRecord(item.args) ? (
                  <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
                    {prettyJson(item.args)}
                  </pre>
                ) : (
                  <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
                    {prettyJson(item)}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      )}

      {remaining.length > 0 && (
        <Collapsible title={<span className="text-[10px] text-gray-400">Other Fields ({remaining.length})</span>}>
          <div className="space-y-1.5">
            {remaining.map(([key, entryValue]) => {
              if (typeof entryValue === 'string' || typeof entryValue === 'number' || typeof entryValue === 'boolean' || entryValue === null) {
                return (
                  <div key={`remaining-${key}`} className="text-[11px] text-gray-300 break-all">
                    <span className="text-gray-500 mr-1">{key}:</span>
                    {String(entryValue)}
                  </div>
                );
              }
              return (
                <Collapsible key={`remaining-collapsible-${key}`} title={<span className="text-[10px] text-gray-400">{key}</span>}>
                  <pre className="text-[10px] text-gray-300 whitespace-pre-wrap break-all max-h-40 overflow-auto scrollbar-thin">
                    {prettyJson(entryValue)}
                  </pre>
                </Collapsible>
              );
            })}
          </div>
        </Collapsible>
      )}
    </div>
  );
}

/* ── Per-event renderers ──────────────────────────────────────────────── */

function ConfigCard({ event }: { event: Extract<LlmEvent, { event: 'config' }> }) {
  const parsed = parseLlmConfigMessage(event.data.message || '');
  const hasMeta = !!(parsed.provider || parsed.model || parsed.backend || parsed.modelType);
  const extraEntries = Object.entries(parsed.fields).filter(
    ([key]) => !['provider', 'model', 'backend'].includes(key)
  );

  return (
    <div className="rounded border border-amber-500/10 bg-[linear-gradient(165deg,rgba(50,35,18,0.20),rgba(28,18,48,0.25))] px-3 py-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <TagBadge tag={event.data.tag} />
        {parsed.provider && <ProviderBadge provider={parsed.provider} />}
        {parsed.modelType && (
          <Badge className="border-blue-400/30 bg-[rgba(20,40,80,0.30)] text-blue-200">
            type: {parsed.modelType}
          </Badge>
        )}
        {parsed.model && <ModelBadge model={parsed.model} />}
        {parsed.providerType && parsed.providerType !== parsed.modelType && (
          <Badge className="border-cyan-400/25 bg-[rgba(20,50,60,0.25)] text-cyan-200">
            provider: {parsed.providerType}
          </Badge>
        )}
        {parsed.backend && (
          <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.25)] text-gray-300">
            backend: {parsed.backend}
          </Badge>
        )}
      </div>
      {!hasMeta && (
        <div className="mt-1 text-[11px] text-gray-300 whitespace-pre-wrap break-all">
          {event.data.message}
        </div>
      )}
      {hasMeta && (
        <div className="mt-1.5 space-y-1">
          {extraEntries.map(([key, value]) => (
            <div key={key} className="text-[10px] text-gray-400 break-all">
              <span className="text-gray-500 mr-1">{key}:</span>
              {value}
            </div>
          ))}
          <Collapsible title={<span className="text-gray-500">Raw Config</span>}>
            <pre className="text-[10px] text-gray-400 whitespace-pre-wrap break-all max-h-28 overflow-auto scrollbar-thin">
              {event.data.message}
            </pre>
          </Collapsible>
        </div>
      )}
    </div>
  );
}

function IterationCard({ event }: { event: Extract<LlmEvent, { event: 'iteration' }> }) {
  const d = event.data;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded border border-cyan-400/15 bg-[linear-gradient(165deg,rgba(20,26,58,0.30),rgba(16,12,45,0.35))] px-3 py-2">
      <Badge className="border-amber-300/40 bg-[rgba(120,90,20,0.30)] text-amber-100 font-bold">#{d.iteration}</Badge>
      <StageBadge stage={d.stage} />
      <span className="text-[10px] text-gray-400">{d.backend}</span>
      <span className="ml-auto text-[10px] text-gray-500">{d.timestamp}</span>
      {typeof d.task_count === 'number' && d.stage === 'completed' && (
        <Badge className="border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200">{d.task_count} tasks</Badge>
      )}
    </div>
  );
}

function LlmCallCard({ event }: { event: Extract<LlmEvent, { event: 'llm_call' }> }) {
  const d = event.data;
  return (
    <div className="rounded border border-blue-400/10 bg-[linear-gradient(165deg,rgba(20,30,60,0.25),rgba(18,14,42,0.30))]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5">
        <span className="relative flex size-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-60" />
          <span className="relative inline-flex size-2 rounded-full bg-blue-400" />
        </span>
        <span className="text-[10px] font-semibold tracking-wide text-blue-300/70">LLM CALL</span>
        <ProviderBadge provider={d.provider} />
        <ModelBadge model={d.model} />
        <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.25)] text-gray-300">{d.prompt_chars.toLocaleString()} chars</Badge>
        <span className="text-[10px] text-gray-500 ml-auto">{event.role}</span>
      </div>
      <div className="px-3 pb-1.5">
        <div className="h-1 w-full rounded-full bg-[rgba(20,40,80,0.30)] overflow-hidden">
          <div className="h-full w-1/3 rounded-full bg-gradient-to-r from-blue-500/40 to-cyan-500/40 animate-pulse" />
        </div>
      </div>
    </div>
  );
}

function LlmResultCard({ event }: { event: Extract<LlmEvent, { event: 'llm_result' }> }) {
  const d = event.data;
  const hasThinking = d.thinking && d.thinking.trim().length > 0;
  const hasOutput =
    (d.output && d.output.trim().length > 0)
    || (d.output_preview && d.output_preview.trim().length > 0)
    || (d.output_json !== undefined && d.output_json !== null);
  const hasError = d.error && d.error.trim().length > 0;
  const contentType = d.content_type || (
    (d.output_json !== undefined && d.output_json !== null)
      ? 'json'
      : ((d.output || '').trim().startsWith('{') ? 'json' : 'text')
  );
  const hasServerStructuredOutput = d.output_json !== undefined && d.output_json !== null;
  const [outputView, setOutputView] = useState<'structured' | 'raw'>('structured');

  let rawOutput = '';
  let displayOutput = '';
  let parsedOutput: unknown | null = null;
  let parseNote = '';
  if (hasOutput) {
    rawOutput = d.output || d.output_preview || '';
    if (contentType === 'json') {
      if (hasServerStructuredOutput) {
        parsedOutput = d.output_json as unknown;
      } else {
        const fallback = parseJsonLikeOutputWithMeta(rawOutput);
        parsedOutput = fallback.value;
        parseNote = fallback.note;
      }
      displayOutput = parsedOutput !== null ? prettyJson(parsedOutput) : rawOutput;
    } else {
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
    if (!hasStructuredOutput) setOutputView('raw');
  }, [hasStructuredOutput]);

  return (
    <div className="rounded border border-cyan-400/15 bg-[linear-gradient(165deg,rgba(20,26,58,0.25),rgba(16,12,45,0.30),rgba(14,20,40,0.35))]">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b border-white/5">
        <StatusDot ok={d.ok} />
        <span className="text-[10px] font-semibold tracking-wide text-cyan-300/70">LLM RESULT</span>
        <ProviderBadge provider={d.provider} />
        <ModelBadge model={d.model} />
        <DurationBadge ms={d.duration_ms} />
        <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.25)] text-gray-300">{d.output_chars.toLocaleString()} chars</Badge>
        <Badge className="border-gray-400/20 bg-[rgba(30,25,50,0.20)] text-gray-400">{contentType}</Badge>
        {structuredSource && (
          <Badge className="border-cyan-400/20 bg-[rgba(20,50,60,0.20)] text-cyan-300">{structuredSource}</Badge>
        )}
        <span className="text-[10px] text-gray-500 ml-auto">{event.role}</span>
      </div>

      {/* Token usage row */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 border-b border-white/5">
        <TokenBadge label="Prompt" value={d.tokens.prompt} />
        <TokenBadge label="Completion" value={d.tokens.completion} />
        <TokenBadge label="Total" value={d.tokens.total} />
        {d.estimated && <span className="text-[9px] text-gray-500 italic">estimated</span>}
      </div>

      {/* Error */}
      {hasError && (
        <div className="mx-3 my-2 rounded border border-red-500/30 bg-[rgba(80,20,15,0.20)] px-3 py-2">
          <pre className="text-[11px] text-red-200 whitespace-pre-wrap break-all">{d.error}</pre>
        </div>
      )}

      {/* Thinking */}
      {hasThinking && (
        <Collapsible
          title={<span className="text-purple-300/80">Thinking ({d.thinking.length.toLocaleString()} chars)</span>}
          className="px-3 py-1.5 border-b border-white/5"
        >
          <pre className="text-[11px] text-gray-300 whitespace-pre-wrap break-all max-h-60 overflow-auto scrollbar-thin">{d.thinking}</pre>
        </Collapsible>
      )}

      {/* Output */}
      {hasOutput && (
        <Collapsible
          title={
            <span className="text-cyan-300/80">
              Output ({d.output_chars.toLocaleString()} chars)
              {d.truncated && <span className="text-gray-500 ml-1">truncated</span>}
            </span>
          }
          defaultOpen={!hasThinking}
          className="px-3 py-1.5"
        >
          {hasStructuredOutput && (
            <div className="flex items-center gap-2 mb-2">
              <button
                onClick={() => setOutputView('structured')}
                className={`rounded px-2 py-0.5 text-[10px] border ${
                  outputView === 'structured'
                    ? 'border-cyan-400/30 bg-[rgba(20,50,60,0.35)] text-cyan-200'
                    : 'border-gray-400/20 bg-[rgba(30,25,50,0.20)] text-gray-400'
                }`}
              >
                Structured
              </button>
              <button
                onClick={() => setOutputView('raw')}
                className={`rounded px-2 py-0.5 text-[10px] border ${
                  outputView === 'raw'
                    ? 'border-cyan-400/30 bg-[rgba(20,50,60,0.35)] text-cyan-200'
                    : 'border-gray-400/20 bg-[rgba(30,25,50,0.20)] text-gray-400'
                }`}
              >
                Raw
              </button>
            </div>
          )}
          {parseNote && <div className="mb-2 text-[10px] text-amber-300/80 break-all">{parseNote}</div>}
          {hasStructuredOutput && outputView === 'structured' ? (
            <StructuredJsonOutput value={parsedOutput} />
          ) : (
            <pre className="text-[11px] text-gray-200 whitespace-pre-wrap break-all max-h-80 overflow-auto scrollbar-thin">{rawOutputForView}</pre>
          )}
        </Collapsible>
      )}
    </div>
  );
}

function InfoCard({ event }: { event: Extract<LlmEvent, { event: 'info' }> }) {
  const d = event.data;
  return (
    <div className="flex items-center gap-2 rounded border border-white/5 bg-[rgba(18,14,42,0.20)] px-3 py-1.5">
      <LevelBadge level={d.level} />
      {d.tag && <TagBadge tag={d.tag} />}
      <span className="text-[11px] text-gray-300 truncate">{d.message}</span>
    </div>
  );
}

function FallbackCard({ event }: { event: LlmEvent }) {
  return (
    <div className="rounded border border-white/5 bg-[rgba(18,14,42,0.15)] px-3 py-1.5">
      <span className="text-[10px] text-gray-500 mr-2">{event.event}</span>
      <pre className="text-[10px] text-gray-400 whitespace-pre-wrap break-all inline">{JSON.stringify(event.data, null, 2)}</pre>
    </div>
  );
}

function StreamEventCard({ event }: { event: LlmEvent }) {
  const data = (event.data && typeof event.data === 'object' ? event.data : {}) as Record<string, unknown>;
  const eventName = String(event.event || '').trim().toLowerCase();
  const message = String(data.message || '').trim();
  const tool = String(data.tool || '').trim();
  const success = data.success;
  const status = success === undefined ? '' : (success ? 'ok' : 'failed');
  const argsPreview = (() => {
    try {
      const raw = data.args && typeof data.args === 'object' ? JSON.stringify(data.args) : '';
      return raw.length > 180 ? `${raw.slice(0, 180)}...` : raw;
    } catch {
      return '';
    }
  })();

  const label =
    eventName === 'thinking_chunk'
      ? '思考流'
      : eventName === 'content_chunk'
      ? '输出流'
      : eventName === 'tool_call'
      ? '工具调用'
      : eventName === 'tool_result'
      ? '工具结果'
      : eventName;

  return (
    <div className="rounded border border-white/5 bg-[rgba(18,14,42,0.20)] px-3 py-2">
      <div className="mb-1 flex items-center gap-2">
        <Badge className="border-cyan-400/30 bg-[rgba(20,50,60,0.35)] text-cyan-200">{label}</Badge>
        {tool && <TagBadge tag={tool} />}
        {status && (
          <Badge className={status === 'ok' ? 'border-emerald-400/30 bg-[rgba(14,45,40,0.30)] text-emerald-200' : 'border-red-400/30 bg-[rgba(80,20,15,0.30)] text-red-200'}>
            {status}
          </Badge>
        )}
      </div>
      {message && <div className="text-[11px] text-gray-200 whitespace-pre-wrap break-words">{message}</div>}
      {argsPreview && <div className="mt-1 text-[10px] text-gray-400 break-all">args: {argsPreview}</div>}
    </div>
  );
}

/* ── Main export ──────────────────────────────────────────────────────── */

export function LlmEventCard({ event }: { event: LlmEvent }) {
  switch (event.event) {
    case 'config':    return <ConfigCard event={event as Extract<LlmEvent, { event: 'config' }>} />;
    case 'iteration': return <IterationCard event={event as Extract<LlmEvent, { event: 'iteration' }>} />;
    case 'llm_call':  return <LlmCallCard event={event as Extract<LlmEvent, { event: 'llm_call' }>} />;
    case 'llm_result': return <LlmResultCard event={event as Extract<LlmEvent, { event: 'llm_result' }>} />;
    case 'info':      return <InfoCard event={event as Extract<LlmEvent, { event: 'info' }>} />;
    case 'thinking_chunk':
    case 'content_chunk':
    case 'tool_call':
    case 'tool_result':
      return <StreamEventCard event={event} />;
    default:          return <FallbackCard event={event} />;
  }
}
