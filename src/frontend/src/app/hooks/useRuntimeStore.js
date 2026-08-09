/**
 * useRuntimeStore - 运行时全局状态管理 (Zustand + Immer)
 *
 * 单一数据源，管理所有运行时状态：
 * - pmStatus, directorStatus
 * - logs, tasks, workers
 * - dialogueEvents
 * - 派生状态 (phase, qualityGate)
 */
import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { enableMapSet } from 'immer';
import { appendLogEntries } from './runtimeParsing';
enableMapSet();
// ============================================================================
// Initial State
// ============================================================================
const initialState = {
    // Connection
    live: false,
    connected: false,
    error: null,
    reconnecting: false,
    attemptCount: 0,
    // Role statuses
    pmStatus: null,
    directorStatus: null,
    engineStatus: null,
    llmStatus: null,
    lancedbStatus: null,
    snapshot: null,
    anthroState: null,
    // Event streams
    dialogueEvents: [],
    executionLogs: [],
    llmStreamEvents: [],
    processStreamEvents: [],
    // Derived
    qualityGate: null,
    currentPhase: 'idle',
    runId: null,
    // Task management
    tasks: [],
    taskProgressMap: new Map(),
    taskTraceMap: new Map(),
    sequentialTraceMap: new Map(),
    // Workers
    workers: [],
    fileEditEvents: [],
};
// ============================================================================
// Store Implementation
// ============================================================================
function normalizeLogEntries(logs, limit) {
    return appendLogEntries([], logs, limit);
}
export const useRuntimeStore = create()(immer((set, get) => ({
    ...initialState,
    // Connection
    setConnectionState: (state) => set((s) => {
        if (state.live !== undefined)
            s.live = state.live;
        if (state.error !== undefined)
            s.error = state.error;
        if (state.reconnecting !== undefined)
            s.reconnecting = state.reconnecting;
        if (state.attemptCount !== undefined)
            s.attemptCount = state.attemptCount;
    }),
    // Role status
    setPmStatus: (status) => set({ pmStatus: status }),
    setDirectorStatus: (status) => set({ directorStatus: status }),
    setEngineStatus: (status) => set({ engineStatus: status }),
    setLlmStatus: (status) => set({ llmStatus: status }),
    setLancedbStatus: (status) => set({ lancedbStatus: status }),
    setSnapshot: (snapshot) => set({ snapshot }),
    setAnthroState: (state) => set({ anthroState: state }),
    // Events
    appendDialogueEvent: (event) => set((s) => {
        s.dialogueEvents = [...s.dialogueEvents, event].slice(-500);
    }),
    setDialogueEvents: (events) => set({ dialogueEvents: events.slice(-500) }),
    appendExecutionLog: (log) => set((s) => {
        s.executionLogs = appendLogEntries([...s.executionLogs], [log], 100);
    }),
    setExecutionLogs: (logs) => set({ executionLogs: normalizeLogEntries(logs, 100) }),
    appendLlmStreamEvent: (log) => set((s) => {
        s.llmStreamEvents = appendLogEntries([...s.llmStreamEvents], [log], 180);
    }),
    setLlmStreamEvents: (logs) => set({ llmStreamEvents: normalizeLogEntries(logs, 180) }),
    appendProcessStreamEvent: (log) => set((s) => {
        s.processStreamEvents = appendLogEntries([...s.processStreamEvents], [log], 240);
    }),
    setProcessStreamEvents: (logs) => set({ processStreamEvents: normalizeLogEntries(logs, 240) }),
    // Derived
    setQualityGate: (data) => set({ qualityGate: data }),
    setCurrentPhase: (phase) => set({ currentPhase: phase }),
    setRunId: (id) => set({ runId: id }),
    // Tasks
    setTasks: (tasks) => set({ tasks }),
    updateTaskProgress: (taskId, progress) => set((s) => {
        const newMap = new Map(s.taskProgressMap);
        newMap.set(taskId, progress);
        s.taskProgressMap = newMap;
    }),
    appendTaskTrace: (event) => set((s) => {
        if (!event.task_id)
            return;
        const newMap = new Map(s.taskTraceMap);
        const traces = newMap.get(event.task_id) || [];
        const updated = [...traces, event].slice(-100);
        newMap.set(event.task_id, updated);
        s.taskTraceMap = newMap;
    }),
    appendSequentialTrace: (runId, event) => set((s) => {
        const newMap = new Map(s.sequentialTraceMap);
        const traces = newMap.get(runId) || [];
        const updated = [...traces, event].slice(-500);
        newMap.set(runId, updated);
        s.sequentialTraceMap = newMap;
    }),
    // Workers
    setWorkers: (workers) => set({ workers }),
    appendFileEditEvent: (event) => set((s) => {
        s.fileEditEvents = [
            ...s.fileEditEvents.filter((item) => item.id !== event.id),
            event,
        ].slice(-50);
    }),
    // Bulk reset
    resetAll: () => set((s) => {
        Object.assign(s, {
            ...initialState,
            taskProgressMap: new Map(),
            taskTraceMap: new Map(),
            sequentialTraceMap: new Map(),
        });
    }),
    resetForWorkspace: () => set((s) => {
        s.pmStatus = null;
        s.directorStatus = null;
        s.engineStatus = null;
        s.llmStatus = null;
        s.lancedbStatus = null;
        s.snapshot = null;
        s.anthroState = null;
        s.dialogueEvents = [];
        s.executionLogs = [];
        s.llmStreamEvents = [];
        s.processStreamEvents = [];
        s.qualityGate = null;
        s.currentPhase = 'idle';
        s.tasks = [];
        s.taskProgressMap = new Map();
        s.taskTraceMap = new Map();
        s.sequentialTraceMap = new Map();
        s.workers = [];
        s.fileEditEvents = [];
        s.runId = null;
        s.live = false;
        s.error = null;
        s.reconnecting = false;
        s.attemptCount = 0;
    }),
})));
// ============================================================================
// Selectors
// ============================================================================
export const selectPmStatus = (s) => s.pmStatus;
export const selectDirectorStatus = (s) => s.directorStatus;
export const selectEngineStatus = (s) => s.engineStatus;
export const selectLlmStatus = (s) => s.llmStatus;
export const selectLancedbStatus = (s) => s.lancedbStatus;
export const selectSnapshot = (s) => s.snapshot;
export const selectAnthroState = (s) => s.anthroState;
export const selectDialogueEvents = (s) => s.dialogueEvents;
export const selectExecutionLogs = (s) => s.executionLogs;
export const selectLlmStreamEvents = (s) => s.llmStreamEvents;
export const selectProcessStreamEvents = (s) => s.processStreamEvents;
export const selectQualityGate = (s) => s.qualityGate;
export const selectCurrentPhase = (s) => s.currentPhase;
export const selectRunId = (s) => s.runId;
export const selectTasks = (s) => s.tasks;
export const selectWorkers = (s) => s.workers;
export const selectFileEditEvents = (s) => s.fileEditEvents;
export const selectTaskProgressMap = (s) => s.taskProgressMap;
export const selectTaskTraceMap = (s) => s.taskTraceMap;
export const selectSequentialTraceMap = (s) => s.sequentialTraceMap;
export const selectIsConnected = (s) => s.live;
export const selectError = (s) => s.error;
export const selectReconnecting = (s) => s.reconnecting;
