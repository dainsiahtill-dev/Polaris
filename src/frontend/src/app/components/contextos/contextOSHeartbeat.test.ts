import { describe, expect, it } from 'vitest';
import { buildContextOSModel, summarizeRoleContextState } from './contextOSData';
import { buildTelemetryFromStream } from './contextOSTelemetry';
import type { LogEntry } from '@/types/log';

function log(id: string, seconds: number, message: string, eventType: string, level: LogEntry['level'] = 'info'): LogEntry {
  return {
    id,
    timestamp: new Date(Date.UTC(2026, 9, 1, 0, 0, seconds)).toISOString(),
    level,
    source: 'Factory',
    title: 'Factory event',
    message,
    tags: [eventType],
    meta: { streamEvent: eventType, role: 'director', stage: 'director_dispatch' },
  };
}

function modelOf(logs: LogEntry[]) {
  const telemetry = buildTelemetryFromStream([], logs, []);
  return {
    telemetry,
    model: buildContextOSModel({
      usageStats: null, dialogueEvents: [], executionLogs: [], snapshot: null,
      llmRuntimeState: { state: 'READY', blockedRoles: [], requiredRoles: ['director'], lastUpdated: null },
      currentPhase: 'implementation', pmRunning: false, directorRunning: true, telemetry,
    }),
  };
}

describe('ContextOS meaningful event projection', () => {
  it('filters pure stage heartbeats before recent-event limits without deleting telemetry or freshness', () => {
    const logs = [log('started', 0, 'Started stage director_dispatch', 'stage_started')];
    for (let index = 1; index <= 40; index += 1) {
      logs.push(log(`pulse-${index}`, index, 'Stage director_dispatch is still running', 'stage_heartbeat'));
    }
    const { telemetry, model } = modelOf(logs);
    const director = model.roles.find((role) => role.id === 'director')!.internalContext;

    expect(telemetry.events).toHaveLength(41);
    expect(director.eventCount).toBe(41);
    expect(director.lastEventAt).toBe(Date.UTC(2026, 9, 1, 0, 0, 40));
    expect(director.events.map((event) => event.id)).toEqual(['started']);
    expect(model.decisions.map((event) => event.id)).toEqual(['started']);
  });

  it('keeps heartbeat failures, warning signals and meaningful progress', () => {
    const { model } = modelOf([
      log('error', 1, 'Stage director_dispatch heartbeat renewal failed', 'stage_heartbeat', 'error'),
      log('warning', 2, 'Stage director_dispatch is still running', 'stage_heartbeat', 'warning'),
      log('progress', 3, 'Director task 1 completed', 'task_completed'),
    ]);
    expect(model.decisions.map((event) => event.id)).toEqual(['progress', 'warning', 'error']);
  });

  it('hides successful task lease renewal observations but preserves lease loss', () => {
    const { model } = modelOf([
      log('renewed', 1, 'Director task 1 heartbeat_renewed', 'task_updated'),
      log('lost', 2, 'Director task 1 heartbeat renewal failed', 'heartbeat_failed', 'error'),
    ]);
    expect(model.decisions.map((event) => event.id)).toEqual(['lost']);
  });

  it('does not hide an LLM response that quotes a heartbeat message', () => {
    const response = log('response', 1, 'Stage director_dispatch is still running', 'llm_completed');
    response.source = 'Director';
    response.meta = { ...response.meta, channel: 'llm' };
    const { model } = modelOf([response]);
    expect(model.decisions.map((event) => event.id)).toEqual(['response']);
  });

  it('does not infer that the model never ran from a heartbeat-only observation window', () => {
    const { model } = modelOf([log('pulse', 1, 'Stage director_dispatch is still running', 'stage_heartbeat')]);
    const context = model.roles.find((role) => role.id === 'director')!.internalContext;
    const summary = summarizeRoleContextState(context);
    expect(summary.headline).toContain('本窗口');
    expect(summary.headline).not.toContain('还未真正调用模型');
    expect(summary.detail).toContain('不能据此断言');
  });
});
