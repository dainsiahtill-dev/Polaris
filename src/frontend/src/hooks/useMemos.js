import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { memoService, fileService } from '@/services/api';
export function useMemos(options = {}) {
    const { workspace, autoLoad = true } = options;
    const workspaceReady = useMemo(() => {
        if (!Object.prototype.hasOwnProperty.call(options, 'workspace')) {
            return true;
        }
        return String(workspace || '').trim().length > 0;
    }, [options, workspace]);
    const [memoItems, setMemoItems] = useState([]);
    const [memoSelected, setMemoSelected] = useState(null);
    const [memoData, setMemoData] = useState({ content: '', mtime: '' });
    const [memoLoading, setMemoLoading] = useState(false);
    const [memoError, setMemoError] = useState(null);
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
        try {
            const result = await memoService.list(200);
            if (result.ok && result.data) {
                const items = Array.isArray(result.data.items) ? result.data.items : [];
                setMemoItems(items);
                setMemoSelected((current) => {
                    if (current) {
                        const stillExists = items.find((item) => item.path === current.path);
                        if (stillExists) {
                            return current;
                        }
                        return items.length > 0 ? items[0] : null;
                    }
                    return items.length > 0 ? items[0] : null;
                });
            }
            else {
                setMemoError(result.error || 'Failed to list memos');
            }
        }
        catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to list memos';
            setMemoItems([]);
            setMemoSelected(null);
            setMemoError(message);
        }
    }, [workspaceReady]);
    const loadMemoContent = useCallback(async (item) => {
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
        try {
            const result = await fileService.read(item.path);
            setMemoLoading(false);
            if (result.ok && result.data) {
                setMemoData(result.data);
            }
            else {
                setMemoError(result.error || 'Failed to read memo');
                setMemoData({ content: '', mtime: '' });
            }
        }
        catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to read memo';
            setMemoLoading(false);
            setMemoError(message);
            setMemoData({ content: '', mtime: '' });
        }
    }, [workspace, workspaceReady]);
    const selectMemo = useCallback((item) => {
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
            void loadMemoList();
        }
    }, [autoLoad, workspaceReady, loadMemoList]);
    useEffect(() => {
        void loadMemoContent(memoSelected);
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
