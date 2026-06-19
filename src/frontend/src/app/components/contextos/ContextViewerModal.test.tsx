import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ContextViewerModal } from './ContextViewerModal';
import type { ViewModelPayload } from './contextosViewModel';

vi.mock('@/api', () => ({
  apiFetch: vi.fn(),
}));

vi.mock('@/runtime/transport', () => ({
  useRuntimeTransport: () => ({
    subscribeChannels: () => () => {},
    registerMessageHandler: () => () => {},
  }),
}));

import { apiFetch } from '@/api';

const mockedApiFetch = apiFetch as unknown as ReturnType<typeof vi.fn>;

function makePayload(overrides: Partial<ViewModelPayload> = {}): ViewModelPayload {
  return {
    schema_version: 1,
    hash: 'abc123def456',
    trace_id: 'trace-1',
    call_id: 'call-1',
    messages: [
      { role: 'system', content: 'You are a helpful assistant.' },
      { role: 'user', content: 'Hi' },
    ],
    stored_at: '2026-06-19T00:00:00Z',
    message_count: 2,
    total_chars: 42,
    ...overrides,
  };
}

function mockFetchOk(payload: ViewModelPayload) {
  mockedApiFetch.mockResolvedValueOnce({
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  });
}

describe('ContextViewerModal', () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders empty state when contextSnapshotRef is null', () => {
    render(<ContextViewerModal contextSnapshotRef={null} roleId="pm" onClose={vi.fn()} />);
    expect(screen.getByTestId('contextos-viewer-empty')).toBeTruthy();
  });

  it('renders loading spinner then content on apiFetch resolving with 2-message payload', async () => {
    const payload = makePayload();
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    expect(screen.getByTestId('contextos-viewer-loading')).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryByTestId('contextos-viewer-loading')).toBeNull();
    });
    expect(screen.getByTestId('contextos-viewer-meta-call').textContent).toContain('call-1');
    expect(screen.getByText('You are a helpful assistant.')).toBeTruthy();
  });

  it('renders meta bar with call/trace/stored_at/message_count/total_chars chips', async () => {
    const payload = makePayload({ total_chars: 12345, message_count: 2 });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-meta-count'));
    expect(screen.getByTestId('contextos-viewer-meta-call').textContent).toContain('call: call-1');
    expect(screen.getByTestId('contextos-viewer-meta-trace').textContent).toContain('trace: trace-1');
    expect(screen.getByTestId('contextos-viewer-meta-stored').textContent).toBeTruthy();
    expect(screen.getByTestId('contextos-viewer-meta-count').textContent).toContain('2 条消息');
    expect(screen.getByTestId('contextos-viewer-meta-count').textContent).toContain('12,345 字符');
  });

  it('shows ~N tok (估算) chip per message', async () => {
    const payload = makePayload({
      messages: [{ role: 'assistant', content: 'Hello world' }],
    });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-msg-0'));
    const card = screen.getByTestId('contextos-msg-0');
    expect(card.textContent).toMatch(/~\d+ tok/);
    expect(card.textContent).toContain('(估算)');
  });

  it('filters messages via search input and shows match count', async () => {
    const payload = makePayload({
      messages: [
        { role: 'system', content: 'You are a helpful assistant.' },
        { role: 'user', content: 'Tell me about apples.' },
        { role: 'assistant', content: 'Apples are red.' },
      ],
    });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-search'));
    fireEvent.change(screen.getByTestId('contextos-viewer-search'), {
      target: { value: 'apples' },
    });
    await waitFor(() => {
      const count = screen.getByTestId('contextos-viewer-search-count');
      expect(count.textContent).toContain('命中');
    });
    // Two messages mention apples (user + assistant); system is filtered out.
    // Use data-role (which only exists on the message card itself) to count cards.
    const cards = document.querySelectorAll('[data-role]');
    expect(cards.length).toBe(2);
    // Verify the system message (which doesn't mention apples) is NOT rendered.
    expect(screen.queryByText('You are a helpful assistant.')).toBeNull();
    // Verify the two matching messages ARE rendered.
    expect(screen.getByText('Tell me about apples.')).toBeTruthy();
    expect(screen.getByText('Apples are red.')).toBeTruthy();
  });

  it('toggles group-by-role and reveals section headers + sticky anchor nav', async () => {
    const payload = makePayload({
      messages: [
        { role: 'system', content: 'sys' },
        { role: 'user', content: 'u1' },
        { role: 'user', content: 'u2' },
      ],
    });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-group-toggle'));
    fireEvent.click(screen.getByTestId('contextos-viewer-group-toggle'));
    expect(screen.getByTestId('contextos-viewer-anchor-nav')).toBeTruthy();
    expect(screen.getByTestId('contextos-group-system')).toBeTruthy();
    expect(screen.getByTestId('contextos-group-user')).toBeTruthy();
  });

  it('global copy button writes markdown containing role labels and --- separators', async () => {
    const payload = makePayload({
      messages: [
        { role: 'system', content: 'sys' },
        { role: 'assistant', content: 'reply' },
      ],
    });
    mockFetchOk(payload);
    const writeText = vi.fn().mockResolvedValue(undefined);
    const originalClipboard = (navigator as { clipboard?: { writeText: typeof writeText } }).clipboard;
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });

    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-copy-all'));
    fireEvent.click(screen.getByTestId('contextos-viewer-copy-all'));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const written = writeText.mock.calls[0]?.[0] as string;
    expect(written).toContain('# Context Snapshot');
    expect(written).toContain('[系统提示]');
    expect(written).toContain('[助手]');
    expect(written).toContain('\n\n---\n\n');

    // restore
    Object.defineProperty(navigator, 'clipboard', { value: originalClipboard, configurable: true });
  });

  it('renders a tool message with valid JSON content as CodeBlock with data-lang="json"', async () => {
    const payload = makePayload({
      messages: [
        {
          role: 'tool',
          content: '{"status":"ok","count":3}',
          tool_call_id: 'call_x',
        },
      ],
    });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-msg-0'));
    const block = document.querySelector('[data-lang="json"]');
    expect(block).toBeTruthy();
    expect(block?.textContent).toContain('"status": "ok"');
    expect(screen.getByTestId('contextos-msg-0-formatted')).toBeTruthy();
  });

  it('Escape key calls onClose', async () => {
    mockFetchOk(makePayload());
    const onClose = vi.fn();
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={onClose} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-body'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('clicking on backdrop calls onClose', async () => {
    mockFetchOk(makePayload());
    const onClose = vi.fn();
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={onClose} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-modal'));
    const modal = screen.getByTestId('contextos-viewer-modal');
    fireEvent.click(modal, { target: modal });
    expect(onClose).toHaveBeenCalled();
  });

  it('renders ErrorState on API 500 and retry re-invokes apiFetch', async () => {
    mockedApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'internal error',
      json: async () => {
        throw new Error('not json');
      },
    });
    // retry 后 200
    mockFetchOk(makePayload({ messages: [{ role: 'user', content: 'retry ok' }] }));

    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-error'));
    expect(screen.getByTestId('contextos-viewer-error').textContent).toContain('加载失败');
    fireEvent.click(screen.getByText('重试'));
    await waitFor(() => {
      expect(screen.queryByTestId('contextos-viewer-error')).toBeNull();
    });
    expect(mockedApiFetch).toHaveBeenCalledTimes(2);
  });
});