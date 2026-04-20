import { useState, useCallback, useEffect } from 'react';
import { memoService, fileService } from '@/services/api';
import type { MemoItem } from '@/app/components/MemoPanel';
import type { FilePayload } from '@/app/types/appContracts';

export interface UseMemosOptions {
  workspace?: string;
  autoLoad?: boolean;
}

export function useMemos(options: UseMemosOptions = {}) {
  const { workspace, autoLoad = true } = options;

  const [memoItems, setMemoItems] = useState<MemoItem[]>([]);
  const [memoSelected, setMemoSelected] = useState<MemoItem | null>(null);
  const [memoData, setMemoData] = useState<FilePayload>({ content: '', mtime: '' });
  const [memoLoading, setMemoLoading] = useState(false);
  const [memoError, setMemoError] = useState<string | null>(null);
  const [memoCollapsed, setMemoCollapsed] = useState(false);

  const loadMemoList = useCallback(async () => {
    setMemoError(null);
    const result = await memoService.list(200);

    if (result.ok && result.data) {
      const items = Array.isArray(result.data.items) ? result.data.items : [];
      setMemoItems(items as MemoItem[]);

      if (memoSelected) {
        const stillExists = items.find((item) => item.path === memoSelected.path);
        if (!stillExists && items.length > 0) {
          setMemoSelected(items[0] as MemoItem);
        } else if (!stillExists) {
          setMemoSelected(null);
        }
      } else if (items.length > 0) {
        setMemoSelected(items[0] as MemoItem);
      }
    } else {
      setMemoError(result.error || 'Failed to list memos');
    }
  }, [memoSelected]);

  const loadMemoContent = useCallback(async (item: MemoItem | null) => {
    if (!item) {
      setMemoData({ content: '', mtime: '' });
      setMemoError(null);
      return;
    }

    setMemoLoading(true);
    setMemoError(null);

    const result = await fileService.read(item.path);

    setMemoLoading(false);

    if (result.ok && result.data) {
      setMemoData(result.data);
    } else {
      setMemoError(result.error || 'Failed to read memo');
      setMemoData({ content: '', mtime: '' });
    }
  }, []);

  const selectMemo = useCallback((item: MemoItem | null) => {
    setMemoSelected(item);
  }, []);

  const refresh = useCallback(async () => {
    await loadMemoList();
    if (memoSelected) {
      await loadMemoContent(memoSelected);
    }
  }, [loadMemoList, loadMemoContent, memoSelected]);

  useEffect(() => {
    if (autoLoad) {
      loadMemoList();
    }
  }, [autoLoad, workspace, loadMemoList]);

  useEffect(() => {
    loadMemoContent(memoSelected);
  }, [memoSelected?.path, workspace]);

  return {
    memoItems,
    memoSelected,
    memoData,
    memoLoading,
    memoError,
    memoCollapsed,
    setMemoCollapsed,
    selectMemo,
    loadMemoList,
    loadMemoContent,
    refresh,
  };
}
