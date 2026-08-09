import { useState, useCallback, useRef, useEffect } from 'react';
import { readFile, readJsonFile } from '@/services';
export function useFileManager(options = {}) {
    const { workspace } = options;
    const [selectedFile, setSelectedFile] = useState(null);
    const [fileData, setFileData] = useState({ content: '', mtime: '' });
    const [fileLoading, setFileLoading] = useState(false);
    const [fileError, setFileError] = useState(null);
    const [fileBadge, setFileBadge] = useState(null);
    const abortControllerRef = useRef(null);
    const selectFile = useCallback((file) => {
        setSelectedFile(file);
    }, []);
    const loadFile = useCallback(async (file) => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        if (!file) {
            setFileData({ content: '', mtime: '' });
            setFileError(null);
            setFileBadge(null);
            return;
        }
        const controller = new AbortController();
        abortControllerRef.current = controller;
        setFileLoading(true);
        setFileError(null);
        setFileBadge(null);
        const result = await readFile(file.path);
        if (controller.signal.aborted)
            return;
        setFileLoading(false);
        if (result.ok && result.data) {
            setFileData(result.data);
        }
        else {
            setFileError(result.error || 'Failed to read file');
            setFileData({ content: '', mtime: '' });
        }
        if (file.id === 'qa' || file.id === 'director-result') {
            const resultBadge = await readFile('runtime/results/director.result.json', 200);
            if (controller.signal.aborted)
                return;
            if (resultBadge.ok && resultBadge.data?.content) {
                const parsedResult = await readJsonFile('runtime/results/director.result.json', 200);
                if (controller.signal.aborted)
                    return;
                if (parsedResult.ok && parsedResult.data) {
                    const { status, acceptance } = parsedResult.data;
                    const normalizedStatus = status?.trim().toUpperCase() ?? '';
                    if (acceptance === true || normalizedStatus === 'SUCCESS') {
                        setFileBadge({ text: '✓ PASSED', tone: 'green' });
                    }
                    else if (acceptance === false || normalizedStatus === 'FAIL') {
                        setFileBadge({ text: '✗ FAILED', tone: 'red' });
                    }
                    else if (normalizedStatus) {
                        setFileBadge({ text: normalizedStatus, tone: 'yellow' });
                    }
                }
            }
        }
    }, []);
    const refresh = useCallback(() => {
        if (selectedFile) {
            loadFile(selectedFile);
        }
    }, [selectedFile, loadFile]);
    useEffect(() => {
        loadFile(selectedFile);
        return () => {
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
            }
        };
    }, [selectedFile?.path, workspace]);
    const appendContent = useCallback((newContent) => {
        setFileData(prev => ({
            content: prev.content ? `${prev.content}\n${newContent}` : newContent,
            mtime: prev.mtime,
        }));
    }, []);
    const setContent = useCallback((content, mtime) => {
        setFileData({ content, mtime: mtime || fileData.mtime });
    }, [fileData.mtime]);
    return {
        selectedFile,
        fileData,
        fileLoading,
        fileError,
        fileBadge,
        selectFile,
        loadFile,
        refresh,
        appendContent,
        setContent,
        clearFile: () => {
            setSelectedFile(null);
            setFileData({ content: '', mtime: '' });
            setFileError(null);
            setFileBadge(null);
        },
    };
}
