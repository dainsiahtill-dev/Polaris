import { useState, useCallback, useEffect } from 'react';
import { fileService } from '@/services/api';
export function useMemory(options = {}) {
    const { showMemory = false, workspace, ramdiskRoot } = options;
    const [memoryData, setMemoryData] = useState({ content: '', mtime: '' });
    const [memoryLoading, setMemoryLoading] = useState(false);
    const [memoryError, setMemoryError] = useState(null);
    const [memoryCollapsed, setMemoryCollapsed] = useState(false);
    const load = useCallback(async () => {
        if (!showMemory) {
            setMemoryData({ content: '', mtime: '' });
            setMemoryError(null);
            return;
        }
        setMemoryLoading(true);
        setMemoryError(null);
        const result = await fileService.read('runtime/memory/last_state.json', 200);
        setMemoryLoading(false);
        if (result.ok && result.data) {
            setMemoryData({ content: result.data.content || '', mtime: result.data.mtime || '' });
        }
        else {
            setMemoryError(result.error || 'Failed to read memory');
            setMemoryData({ content: '', mtime: '' });
        }
    }, [showMemory]);
    const updateContent = useCallback((content, mtime) => {
        setMemoryData({ content, mtime: mtime || memoryData.mtime });
        setMemoryError(null);
    }, [memoryData.mtime]);
    useEffect(() => {
        load();
    }, [showMemory, workspace, ramdiskRoot]);
    return {
        memoryData,
        memoryLoading,
        memoryError,
        memoryCollapsed,
        setMemoryCollapsed,
        load,
        updateContent,
    };
}
