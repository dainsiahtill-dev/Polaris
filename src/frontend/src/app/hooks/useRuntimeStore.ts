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
import type { BackendStatus, EngineStatus, LlmStatus, LanceDbStatus, AnthroState, SnapshotPayload } from '@/app/types/appContracts';
import type { DialogueEvent } from '@/app/components/DialoguePanel';
import type { QualityGateData } from '@/app/components/pm';
import type { LogEntry } from '@/types/log';
import { type PmTask } from '@/types/task';
import type { TaskTraceEvent } from '../types/taskTrace';

// ============================================================================
// Types
// ============================================================================

export interface FileEditEvent {
  id: string;
  filePath: string;
  operation: 'create' | 'modify' | 'delete';
  contentSize: number;
  taskId?: string;
  timestamp: string;
  patch?: string;
  addedLines?: number;
  deletedLines?: number;
  modifiedLines?: number;
}

export interface TaskProgress {
  phase?: string;
  phaseIndex?: number;
  phaseTotal?: number;
  retryCount?: number;
  maxRetries?: number;
  currentFile?: string;
}

export interface SequentialTraceEvent {
  eventType: string;
  runId: string;
  role: string;
  taskId: string;
  stepIndex: number;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface RuntimeWorkerState {
  id: string;
  name?: string;
  status: 'idle' | 'busy' | 'stopping' | 'stopped' | 'failed' | string;
  currentTaskId?: string;
  healthy?: boolean;
  tasksCompleted?: number;
  tasksFailed?: number;
}

// ============================================================================
// Store Interface
// ============================================================================

export interface RuntimeState {
  // Connection state
  live: boolean;
  connected: boolean;
  error: string | null;
  reconnecting: boolean;
  attemptCount: number;

  // Role statuses
  pmStatus: BackendStatus | null;
  directorStatus: BackendStatus | null;
  engineStatus: EngineStatus | null;
  llmStatus: LlmStatus | null;
  lancedbStatus: LanceDbStatus | null;
  snapshot: SnapshotPayload | null;
  anthroState: AnthroState | null;

  // Event streams
  dialogueEvents: DialogueEvent[];
  executionLogs: LogEntry[];
  llmStreamEvents: LogEntry[];
  processStreamEvents: LogEntry[];

  // Derived state
  qualityGate: QualityGateData | null;
  currentPhase: string;
  runId: string | null;

  // Task management
  tasks: PmTask[];
  taskProgressMap: Map<string, TaskProgress>;
  taskTraceMap: Map<string, TaskTraceEvent[]>;
  sequentialTraceMap: Map<string, SequentialTraceEvent[]>;

  // Worker management
  workers: RuntimeWorkerState[];
  fileEditEvents: FileEditEvent[];

  // Actions - Connection
  setConnectionState: (state: Partial<Pick<RuntimeState, 'live' | 'error' | 'reconnecting' | 'attemptCount'>>) => void;

  // Actions - Role status
  setPmStatus: (status: BackendStatus | null) => void;
  setDirectorStatus: (status: BackendStatus | null) => void;
  setEngineStatus: (status: EngineStatus | null) => void;
  setLlmStatus: (status: LlmStatus | null) => void;
  setLancedbStatus: (status: LanceDbStatus | null) => void;
  setSnapshot: (snapshot: SnapshotPayload | null) => void;
  setAnthroState: (state: AnthroState | null) => void;

  // Actions - Events
  appendDialogueEvent: (event: DialogueEvent) => void;
  setDialogueEvents: (events: DialogueEvent[]) => void;
  appendExecutionLog: (log: LogEntry) => void;
  setExecutionLogs: (logs: LogEntry[]) => void;
  appendLlmStreamEvent: (log: LogEntry) => void;
  setLlmStreamEvents: (logs: LogEntry[]) => void;
  appendProcessStreamEvent: (log: LogEntry) => void;
  setProcessStreamEvents: (logs: LogEntry[]) => void;

  // Actions - Derived
  setQualityGate: (data: QualityGateData | null) => void;
  setCurrentPhase: (phase: string) => void;
  setRunId: (id: string | null) => void;

  // Actions - Tasks
  setTasks: (tasks: PmTask[]) => void;
  updateTaskProgress: (taskId: string, progress: TaskProgress) => void;
  appendTaskTrace: (event: TaskTraceEvent) => void;
  appendSequentialTrace: (runId: string, event: SequentialTraceEvent) => void;

  // Actions - Workers
  setWorkers: (workers: RuntimeWorkerState[]) => void;
  appendFileEditEvent: (event: FileEditEvent) => void;

  // Actions - Bulk reset
  resetAll: () => void;
  resetForWorkspace: () => void;
}

// ============================================================================
// Initial State
// ============================================================================

const initialState: Omit<RuntimeState,
  | 'setConnectionState'
  | 'setPmStatus'
  | 'setDirectorStatus'
  | 'setEngineStatus'
  | 'setLlmStatus'
  | 'setLancedbStatus'
  | 'setSnapshot'
  | 'setAnthroState'
  | 'appendDialogueEvent'
  | 'setDialogueEvents'
  | 'appendExecutionLog'
  | 'setExecutionLogs'
  | 'appendLlmStreamEvent'
  | 'setLlmStreamEvents'
  | 'appendProcessStreamEvent'
  | 'setProcessStreamEvents'
  | 'setQualityGate'
  | 'setCurrentPhase'
  | 'setRunId'
  | 'setTasks'
  | 'updateTaskProgress'
  | 'appendTaskTrace'
  | 'appendSequentialTrace'
  | 'setWorkers'
  | 'appendFileEditEvent'
  | 'resetAll'
  | 'resetForWorkspace'> = {
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

export const useRuntimeStore = create<RuntimeState>()(
  immer((set, get) => ({
    ...initialState,

    // Connection
    setConnectionState: (state) =>
      set((s) => {
        if (state.live !== undefined) s.live = state.live;
        if (state.error !== undefined) s.error = state.error;
        if (state.reconnecting !== undefined) s.reconnecting = state.reconnecting;
        if (state.attemptCount !== undefined) s.attemptCount = state.attemptCount;
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
    appendDialogueEvent: (event) =>
      set((s) => {
        s.dialogueEvents = [...s.dialogueEvents, event].slice(-500);
      }),

    setDialogueEvents: (events) =>
      set({ dialogueEvents: events.slice(-500) }),

    appendExecutionLog: (log) =>
      set((s) => {
        s.executionLogs = [...s.executionLogs, log].slice(-100);
      }),

    setExecutionLogs: (logs) =>
      set({ executionLogs: logs.slice(-100) }),

    appendLlmStreamEvent: (log) =>
      set((s) => {
        s.llmStreamEvents = [...s.llmStreamEvents, log].slice(-180);
      }),

    setLlmStreamEvents: (logs) =>
      set({ llmStreamEvents: logs.slice(-180) }),

    appendProcessStreamEvent: (log) =>
      set((s) => {
        s.processStreamEvents = [...s.processStreamEvents, log].slice(-240);
      }),

    setProcessStreamEvents: (logs) =>
      set({ processStreamEvents: logs.slice(-240) }),

    // Derived
    setQualityGate: (data) => set({ qualityGate: data }),
    setCurrentPhase: (phase) => set({ currentPhase: phase }),
    setRunId: (id) => set({ runId: id }),

    // Tasks
    setTasks: (tasks) => set({ tasks }),

    updateTaskProgress: (taskId, progress) =>
      set((s) => {
        const newMap = new Map(s.taskProgressMap);
        newMap.set(taskId, progress);
        s.taskProgressMap = newMap;
      }),

    appendTaskTrace: (event) =>
      set((s) => {
        if (!event.task_id) return;
        const newMap = new Map(s.taskTraceMap);
        const traces = newMap.get(event.task_id) || [];
        const updated = [...traces, event].slice(-100);
        newMap.set(event.task_id, updated);
        s.taskTraceMap = newMap;
      }),

    appendSequentialTrace: (runId, event) =>
      set((s) => {
        const newMap = new Map(s.sequentialTraceMap);
        const traces = newMap.get(runId) || [];
        const updated = [...traces, event].slice(-500);
        newMap.set(runId, updated);
        s.sequentialTraceMap = newMap;
      }),

    // Workers
    setWorkers: (workers) => set({ workers }),

    appendFileEditEvent: (event) =>
      set((s) => {
        s.fileEditEvents = [...s.fileEditEvents, event].slice(-50);
      }),

    // Bulk reset
    resetAll: () =>
      set((s) => {
        Object.assign(s, {
          ...initialState,
          taskProgressMap: new Map(),
          taskTraceMap: new Map(),
          sequentialTraceMap: new Map(),
        });
      }),

    resetForWorkspace: () =>
      set((s) => {
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
  }))
);

// ============================================================================
// Selectors
// ============================================================================

export const selectPmStatus = (s: RuntimeState) => s.pmStatus;
export const selectDirectorStatus = (s: RuntimeState) => s.directorStatus;
export const selectEngineStatus = (s: RuntimeState) => s.engineStatus;
export const selectLlmStatus = (s: RuntimeState) => s.llmStatus;
export const selectLancedbStatus = (s: RuntimeState) => s.lancedbStatus;
export const selectSnapshot = (s: RuntimeState) => s.snapshot;
export const selectAnthroState = (s: RuntimeState) => s.anthroState;

export const selectDialogueEvents = (s: RuntimeState) => s.dialogueEvents;
export const selectExecutionLogs = (s: RuntimeState) => s.executionLogs;
export const selectLlmStreamEvents = (s: RuntimeState) => s.llmStreamEvents;
export const selectProcessStreamEvents = (s: RuntimeState) => s.processStreamEvents;

export const selectQualityGate = (s: RuntimeState) => s.qualityGate;
export const selectCurrentPhase = (s: RuntimeState) => s.currentPhase;
export const selectRunId = (s: RuntimeState) => s.runId;

export const selectTasks = (s: RuntimeState) => s.tasks;
export const selectWorkers = (s: RuntimeState) => s.workers;
export const selectFileEditEvents = (s: RuntimeState) => s.fileEditEvents;

export const selectTaskProgressMap = (s: RuntimeState) => s.taskProgressMap;
export const selectTaskTraceMap = (s: RuntimeState) => s.taskTraceMap;
export const selectSequentialTraceMap = (s: RuntimeState) => s.sequentialTraceMap;

export const selectIsConnected = (s: RuntimeState) => s.live;
export const selectError = (s: RuntimeState) => s.error;
export const selectReconnecting = (s: RuntimeState) => s.reconnecting;