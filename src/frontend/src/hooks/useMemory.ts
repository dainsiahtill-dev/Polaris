import { useState, useCallback, useEffect } from 'react';
import { fileService } from '@/services/api';
import type { FilePayload } from '@/app/types/appContracts';

export interface UseMemoryOptions {
  showMemory?: boolean;
  workspace?: string;
  ramdiskRoot?: string;
}

export function useMemory(options: UseMemoryOptions = {}) {
  const { showMemory = false, workspace, ramdiskRoot } = options;

  const [memoryData, setMemoryData] = useState<FilePayload>({ content: '', mtime: '' });
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryError, setMemoryError] = useState<string | null>(null);
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
    } else {
      setMemoryError(result.error || 'Failed to read memory');
      setMemoryData({ content: '', mtime: '' });
    }
  }, [showMemory]);

  const updateContent = useCallback((content: string, mtime?: string) => {
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

