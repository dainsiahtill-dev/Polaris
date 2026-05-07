import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listMock = vi.fn();
const readMock = vi.fn();

vi.mock('@/services/api', () => ({
  memoService: {
    list: (...args: unknown[]) => listMock(...args),
  },
  fileService: {
    read: (...args: unknown[]) => readMock(...args),
  },
}));

import { useMemos } from './useMemos';

describe('useMemos', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockResolvedValue({
      ok: true,
      data: {
        items: [{ path: 'runtime/memos/first.md', name: 'first' }],
        count: 1,
      },
    });
    readMock.mockResolvedValue({
      ok: true,
      data: { content: 'memo content', mtime: '2026-05-07T00:00:00Z' },
    });
  });

  it('does not request memos until an explicitly controlled workspace is ready', async () => {
    const { rerender } = renderHook(
      ({ workspace }) => useMemos({ workspace }),
      { initialProps: { workspace: '' } },
    );

    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listMock).not.toHaveBeenCalled();
    expect(readMock).not.toHaveBeenCalled();

    rerender({ workspace: 'C:/workspace' });

    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(1);
      expect(readMock).toHaveBeenCalledTimes(1);
    });
    expect(readMock).toHaveBeenCalledWith('runtime/memos/first.md');
  });

  it('does not list memos again when the first memo is selected automatically', async () => {
    const { result } = renderHook(() => useMemos({ workspace: 'C:/workspace' }));

    await waitFor(() => {
      expect(result.current.memoSelected?.path).toBe('runtime/memos/first.md');
    });

    expect(listMock).toHaveBeenCalledTimes(1);
    expect(readMock).toHaveBeenCalledTimes(1);
  });

  it('allows an explicit refresh to reload the selected memo content', async () => {
    const { result } = renderHook(() => useMemos({ workspace: 'C:/workspace' }));

    await waitFor(() => {
      expect(readMock).toHaveBeenCalledTimes(1);
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(listMock).toHaveBeenCalledTimes(2);
    expect(readMock).toHaveBeenCalledTimes(2);
  });
});
