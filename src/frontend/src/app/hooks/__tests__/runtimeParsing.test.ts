import { describe, expect, it } from 'vitest';
import {
  extractFileEditEvents,
  extractRuntimeFileEditEvent,
} from '../runtimeParsing';

describe('runtimeParsing file edit event normalization', () => {
  it('normalizes direct websocket file_edit payloads', () => {
    const event = extractFileEditEvents({
      timestamp: '2026-05-07T01:00:00.000Z',
      event: {
        file_path: 'src/new.ts',
        operation: 'create',
        content_size: 42,
        task_id: 'PM-1',
        added_lines: '3',
      },
    });

    expect(event).toMatchObject({
      id: 'src/new.ts-2026-05-07T01:00:00.000Z',
      filePath: 'src/new.ts',
      operation: 'create',
      contentSize: 42,
      taskId: 'PM-1',
      timestamp: '2026-05-07T01:00:00.000Z',
      patch: undefined,
      addedLines: 3,
      deletedLines: undefined,
      modifiedLines: undefined,
    });
  });

  it('extracts file edits from runtime.v2 event.file_edit envelopes', () => {
    const event = extractRuntimeFileEditEvent({
      schema_version: 'runtime.v2',
      channel: 'event.file_edit',
      kind: 'file_edit',
      timestamp: '2026-05-07T02:00:00.000Z',
      payload: {
        raw: {
          file_path: 'src/changed.ts',
          operation: 'modify',
          content_size: 128,
          task_id: 'PM-2',
          patch: [
            '--- a/src/changed.ts',
            '+++ b/src/changed.ts',
            '@@ -1 +1 @@',
            '-old',
            '+new',
          ].join('\n'),
          modified_lines: 1,
        },
      },
    });

    expect(event?.filePath).toBe('src/changed.ts');
    expect(event?.operation).toBe('modify');
    expect(event?.taskId).toBe('PM-2');
    expect(event?.modifiedLines).toBe(1);
    expect(event?.patch).toContain('+new');
    expect(event?.schemaVersion).toBe('runtime.v2');
    expect(event?.sourceChannel).toBe('event.file_edit');
    expect(event?.eventKind).toBe('file_edit');
  });

  it('extracts file edits from runtime event data payloads', () => {
    const event = extractRuntimeFileEditEvent({
      event: 'file_written',
      ts: '2026-05-07T03:00:00.000Z',
      data: {
        filePath: 'src/removed.ts',
        operation: 'delete',
        contentSize: 0,
        taskId: 'PM-3',
        deletedLines: 2,
      },
    });

    expect(event?.filePath).toBe('src/removed.ts');
    expect(event?.operation).toBe('delete');
    expect(event?.taskId).toBe('PM-3');
    expect(event?.deletedLines).toBe(2);
  });

  it('accepts backend filepath and size_bytes aliases', () => {
    const event = extractRuntimeFileEditEvent({
      event: 'file_written',
      timestamp: '2026-05-07T04:00:00.000Z',
      data: {
        filepath: 'src/alias.ts',
        operation: 'modify',
        size_bytes: 256,
      },
    });

    expect(event?.filePath).toBe('src/alias.ts');
    expect(event?.contentSize).toBe(256);
  });
});
