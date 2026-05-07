import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { memoService, fileService } from '@/services/api';
import type { MemoItem } from '@/app/components/MemoPanel';
import type { FilePayload } from '@/app/types/appContracts';

export interface UseMemosOptions {
  workspace?: string;
  autoLoad?: boolean;
}

export function useMemos(options: UseMemosOptions = {}) {
  const { workspace, autoLoad = true } = options;
  const workspaceReady = useMemo(() => {
    if (!Object.prototype.hasOwnProperty.call(options, 'workspace')) {
      return true;
    }
    return String(workspace || '').trim().length > 0;
  }, [options, workspace]);

  const [memoItems, setMemoItems] = useState<MemoItem[]>([]);
  const [memoSelected, setMemoSelected] = useState<MemoItem | null>(null);
  const [memoData, setMemoData] = useState<FilePayload>({ content: '', mtime: '' });
  const [memoLoading, setMemoLoading] = useState(false);
  const [memoError, setMemoError] = useState<string | null>(null);
  const [memoCollapsed, setMemoCollapsed] = useState(false);
  const lastReadKeyRef = useRef('');

  const loadMemoList = useCallback(async () => {
    if (!workspaceReady) {
      setMemoItems([]);
      setMemoSelected(null);
      setMemoData({ content: '', mtime: '' });
      setMemoError(null);
      return;
    }

    setMemoError(null);
    const result = await memoService.list(200);

    if (result.ok && result.data) {
      const items = Array.isArray(result.data.items) ? result.data.items : [];
      setMemoItems(items as MemoItem[]);
      setMemoSelected((current) => {
        if (current) {
          const stillExists = items.find((item) => item.path === current.path);
          if (stillExists) {
            return current;
          }
          return items.length > 0 ? items[0] as MemoItem : null;
        }
        return items.length > 0 ? items[0] as MemoItem : null;
      });
    } else {
      setMemoError(result.error || 'Failed to list memos');
    }
  }, [workspaceReady]);

  const loadMemoContent = useCallback(async (item: MemoItem | null) => {
    if (!workspaceReady || !item) {
      lastReadKeyRef.current = '';
      setMemoData({ content: '', mtime: '' });
      setMemoError(null);
      return;
    }

    const readKey = `${String(workspace || '')}:${item.path}`;
    if (lastReadKeyRef.current === readKey) {
      return;
    }
    lastReadKeyRef.current = readKey;

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
  }, [workspace, workspaceReady]);

  const selectMemo = useCallback((item: MemoItem | null) => {
    setMemoSelected(item);
  }, []);

  const refresh = useCallback(async () => {
    await loadMemoList();
    if (memoSelected) {
      lastReadKeyRef.current = '';
      await loadMemoContent(memoSelected);
    }
  }, [loadMemoList, loadMemoContent, memoSelected]);

  useEffect(() => {
    if (autoLoad && workspaceReady) {
      loadMemoList();
    }
  }, [autoLoad, workspaceReady, loadMemoList]);

  useEffect(() => {
    loadMemoContent(memoSelected);
  }, [loadMemoContent, memoSelected]);

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
