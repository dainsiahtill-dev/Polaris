import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useRuntimeConnectionNotifications } from '../useConnectionNotifications';

const toastMock = vi.hoisted(() => ({
  dismiss: vi.fn(),
  error: vi.fn(() => 'runtime-disconnected-toast'),
  success: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: toastMock,
}));

describe('useRuntimeConnectionNotifications', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('suppresses transient reconnect noise', () => {
    const { rerender } = renderHook(
      ({ live, reconnecting }) => useRuntimeConnectionNotifications({ live, reconnecting }),
      { initialProps: { live: true, reconnecting: false } },
    );

    rerender({ live: false, reconnecting: true });
    act(() => {
      vi.advanceTimersByTime(3999);
    });
    rerender({ live: true, reconnecting: false });
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(toastMock.error).not.toHaveBeenCalled();
    expect(toastMock.success).not.toHaveBeenCalled();
  });

  it('shows recovery only after a sustained outage toast was visible', () => {
    const { rerender } = renderHook(
      ({ live, reconnecting }) => useRuntimeConnectionNotifications({ live, reconnecting }),
      { initialProps: { live: true, reconnecting: false } },
    );

    rerender({ live: false, reconnecting: true });
    act(() => {
      vi.advanceTimersByTime(4000);
    });

    expect(toastMock.error).toHaveBeenCalledWith('连接已断开', {
      description: '正在重新连接...',
      duration: 5000,
    });

    rerender({ live: true, reconnecting: false });

    expect(toastMock.dismiss).toHaveBeenCalledWith('runtime-disconnected-toast');
    expect(toastMock.success).toHaveBeenCalledWith('连接已恢复', {
      description: '实时更新已恢复',
      duration: 3000,
    });
  });
});
