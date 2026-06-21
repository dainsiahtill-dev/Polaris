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

  it('renders message toggle and copy controls without nested buttons', async () => {
    const payload = makePayload({
      messages: [{ role: 'assistant', content: 'Hello world' }],
    });
    mockFetchOk(payload);
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-msg-0'));

    const card = screen.getByTestId('contextos-msg-0');
    expect(card.querySelector('button button')).toBeNull();
    expect(screen.getByTestId('contextos-msg-0-toggle')).toBeTruthy();
    expect(screen.getByTestId('contextos-msg-0-copy')).toBeTruthy();
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

  it('renders a context-missing empty state for structured CONTEXT_NOT_FOUND 404', async () => {
    mockedApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      text: async () =>
        JSON.stringify({
          detail: {
            code: 'CONTEXT_NOT_FOUND',
            message: 'Context snapshot not found for hash abc',
          },
        }),
      json: async () => ({
        detail: {
          code: 'CONTEXT_NOT_FOUND',
          message: 'Context snapshot not found for hash abc',
        },
      }),
    });

    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);

    const missing = await waitFor(() => screen.getByTestId('contextos-viewer-context-missing'));
    expect(missing.textContent).toContain('完整上下文快照不可用');
    expect(screen.queryByTestId('contextos-viewer-error')).toBeNull();
    expect(screen.queryByText(/HTTP 404/)).toBeNull();
  });
});

describe('ContextViewerModal accessibility (Phase 3 hardening)', () => {
  beforeEach(() => {
    mockedApiFetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('dialog has aria-modal="true" and aria-labelledby pointing at the title', () => {
    render(<ContextViewerModal contextSnapshotRef={null} roleId="pm" onClose={vi.fn()} />);
    const dialog = screen.getByTestId('contextos-viewer-modal');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    const labelledBy = dialog.getAttribute('aria-labelledby');
    expect(labelledBy).toBeTruthy();
    const title = labelledBy ? document.getElementById(labelledBy) : null;
    expect(title).toBeTruthy();
    expect(title?.tagName.toLowerCase()).toBe('h2');
  });

  it('dialog has aria-describedby pointing at the meta bar once content loads', async () => {
    mockFetchOk(makePayload());
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-meta-count'));
    const dialog = screen.getByTestId('contextos-viewer-modal');
    const describedBy = dialog.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    const description = describedBy ? document.getElementById(describedBy) : null;
    expect(description).toBeTruthy();
    // description 是 meta bar，应包含 call / trace / count 之一
    expect(description?.textContent).toContain('call:');
  });

  it('loading state has role="status" and aria-live="polite"', () => {
    // 慢响应以保留 loading 态
    mockedApiFetch.mockImplementation(
      () =>
        new Promise(() => {
          // 永不 resolve
        }),
    );
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    const loading = screen.getByTestId('contextos-viewer-loading');
    expect(loading.getAttribute('role')).toBe('status');
    expect(loading.getAttribute('aria-live')).toBe('polite');
    expect(loading.getAttribute('aria-busy')).toBe('true');
  });

  it('error state has role="alert" and aria-live="assertive"', async () => {
    mockedApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      text: async () => 'boom',
      json: async () => {
        throw new Error('not json');
      },
    });
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-error'));
    const error = screen.getByTestId('contextos-viewer-error');
    expect(error.getAttribute('role')).toBe('alert');
    expect(error.getAttribute('aria-live')).toBe('assertive');
  });

  it('focus is moved into the dialog on mount (initial focus inside container)', async () => {
    mockFetchOk(makePayload());
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-close'));
    // 关闭按钮在弹窗内，且应被赋予初始焦点。
    await waitFor(() => {
      const active = document.activeElement as HTMLElement | null;
      const dialog = screen.getByTestId('contextos-viewer-modal');
      expect(dialog.contains(active)).toBe(true);
    });
  });

  it('Tab on last focusable element wraps focus to first focusable element', async () => {
    mockFetchOk(
      makePayload({
        messages: [
          { role: 'user', content: 'a'.repeat(2000) },
        ],
      }),
    );
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-close'));
    // 寻找弹窗内最后一个可聚焦元素（footer 关闭按钮）
    const dialog = screen.getByTestId('contextos-viewer-modal');
    const allButtons = Array.from(
      dialog.querySelectorAll<HTMLElement>('button:not([disabled])'),
    );
    // 最后一个按钮：footer 的"关闭"按钮
    const lastButton = allButtons[allButtons.length - 1];
    expect(lastButton).toBeTruthy();
    lastButton.focus();
    expect(document.activeElement).toBe(lastButton);

    // 在最后一个聚焦元素上按 Tab，应回卷到第一个
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(dialog.contains(document.activeElement as HTMLElement | null)).toBe(true);
    // 焦点应不再停留在原 lastButton
    expect(document.activeElement).not.toBe(lastButton);
  });

  it('Shift+Tab on first focusable element wraps focus to last focusable element', async () => {
    mockFetchOk(makePayload());
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-close'));
    // 等焦点稳定到弹窗内
    await waitFor(() => {
      const dialog = screen.getByTestId('contextos-viewer-modal');
      expect(dialog.contains(document.activeElement as HTMLElement | null)).toBe(true);
    });
    // Shift+Tab 应在首元素上回卷到尾元素（footer 关闭按钮在内容下方）
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    const dialog = screen.getByTestId('contextos-viewer-modal');
    expect(dialog.contains(document.activeElement as HTMLElement | null)).toBe(true);
  });

  it('body scroll is locked while modal is mounted and restored on unmount', async () => {
    const previousOverflow = document.body.style.overflow;
    mockFetchOk(makePayload());
    const { unmount } = render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe(previousOverflow);
  });

  it('focus is restored to the previously-focused element on unmount', async () => {
    const trigger = document.createElement('button');
    trigger.textContent = 'open-modal-trigger';
    document.body.appendChild(trigger);
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    mockFetchOk(makePayload());
    const { unmount } = render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-close'));
    // 焦点应已迁入弹窗
    const dialog = screen.getByTestId('contextos-viewer-modal');
    expect(dialog.contains(document.activeElement as HTMLElement | null)).toBe(true);

    unmount();
    // 焦点归还到原 trigger
    expect(document.activeElement).toBe(trigger);
    document.body.removeChild(trigger);
  });

  it('re-fetches with a new AbortController when contextSnapshotRef changes; old fetch is cancelled', async () => {
    // 第一个 fetch 永远 pending（模拟慢请求）
    let firstResolve: ((value: Response) => void) | null = null;
    mockedApiFetch.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          firstResolve = resolve;
        }),
    );
    // 第二次 fetch 即时返回成功
    mockFetchOk(makePayload({ messages: [{ role: 'user', content: 'second' }] }));

    const { rerender } = render(
      <ContextViewerModal contextSnapshotRef="first-ref" roleId="pm" onClose={vi.fn()} />,
    );
    // 第一次请求已发出但未返回
    expect(mockedApiFetch).toHaveBeenCalledTimes(1);

    // 切换 ref：触发新请求，旧的应被取消
    rerender(<ContextViewerModal contextSnapshotRef="second-ref" roleId="pm" onClose={vi.fn()} />);
    expect(mockedApiFetch).toHaveBeenCalledTimes(2);

    // 让第一次 fetch 在「取消后」resolve：组件应忽略该结果
    await waitFor(() => {
      expect(screen.getByText('second')).toBeTruthy();
    });
    if (firstResolve) {
      firstResolve(
        new Response(JSON.stringify({ messages: [{ role: 'user', content: 'first-wins' }] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
    }
    // 等待一拍让潜在的 setState 触发，再确认 first 的内容没出现
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('first-wins')).toBeNull();
    expect(screen.getByText('second')).toBeTruthy();
  });

  it('search input has aria-label for screen readers', async () => {
    mockFetchOk(makePayload());
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-search'));
    const search = screen.getByTestId('contextos-viewer-search');
    expect(search.getAttribute('aria-label')).toBeTruthy();
  });

  it('group toggle has aria-pressed reflecting state', async () => {
    mockFetchOk(makePayload());
    render(<ContextViewerModal contextSnapshotRef="abc" roleId="pm" onClose={vi.fn()} />);
    await waitFor(() => screen.getByTestId('contextos-viewer-group-toggle'));
    const toggle = screen.getByTestId('contextos-viewer-group-toggle');
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
  });
});
