/**
 * Runtime Store - Zustand 状态管理
 * 管理 PM/Director 运行时的状态
 */
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
// ============================================================================
// Constants
// ============================================================================
const RUNTIME_STORAGE_KEY = 'polaris:runtime';
// ============================================================================
// Store Creation
// ============================================================================
export const useRuntimeStore = create()(persist((set) => ({
    // ============ 初始状态 ============
    pmStatus: 'idle',
    directorStatus: 'idle',
    pmError: null,
    directorError: null,
    activeWorkers: [],
    workerTasks: {},
    taskQueue: [],
    completedTasks: [],
    // ============ 状态设置 ============
    setPmStatus: (status) => set({ pmStatus: status, pmError: status === 'idle' ? null : undefined }),
    setDirectorStatus: (status) => set({ directorStatus: status, directorError: status === 'idle' ? null : undefined }),
    setPmError: (error) => set({ pmError: error, pmStatus: error ? 'error' : undefined }),
    setDirectorError: (error) => set({ directorError: error, directorStatus: error ? 'error' : undefined }),
    // ============ Worker管理 ============
    addWorker: (workerId) => set((state) => ({
        activeWorkers: state.activeWorkers.includes(workerId)
            ? state.activeWorkers
            : [...state.activeWorkers, workerId],
    })),
    removeWorker: (workerId) => set((state) => ({
        activeWorkers: state.activeWorkers.filter((id) => id !== workerId),
        workerTasks: Object.fromEntries(Object.entries(state.workerTasks).filter(([, task]) => task.workerId !== workerId)),
    })),
    updateWorkerTask: (workerId, task) => set((state) => ({
        workerTasks: { ...state.workerTasks, [workerId]: task },
    })),
    clearWorkerTask: (workerId) => set((state) => {
        const newTasks = { ...state.workerTasks };
        delete newTasks[workerId];
        return { workerTasks: newTasks };
    }),
    // ============ 任务队列 ============
    addTask: (taskId) => set((state) => ({
        taskQueue: state.taskQueue.includes(taskId)
            ? state.taskQueue
            : [...state.taskQueue, taskId],
    })),
    removeTask: (taskId) => set((state) => ({
        taskQueue: state.taskQueue.filter((id) => id !== taskId),
    })),
    completeTask: (taskId) => set((state) => ({
        taskQueue: state.taskQueue.filter((id) => id !== taskId),
        completedTasks: [...state.completedTasks, taskId],
    })),
    clearCompletedTasks: () => set({ completedTasks: [] }),
    // ============ 批量操作 ============
    resetAllStatus: () => set({
        pmStatus: 'idle',
        directorStatus: 'idle',
        pmError: null,
        directorError: null,
        activeWorkers: [],
        workerTasks: {},
        taskQueue: [],
        completedTasks: [],
    }),
}), {
    name: RUNTIME_STORAGE_KEY,
    partialize: (state) => ({
    // 不持久化运行时状态，只保留配置
    }),
}));
// ============================================================================
// Selector Hooks
// ============================================================================
/** PM 运行时状态 */
export const usePmRuntimeStatus = () => useRuntimeStore((state) => ({
    status: state.pmStatus,
    error: state.pmError,
    setStatus: state.setPmStatus,
    setError: state.setPmError,
}));
/** Director 运行时状态 */
export const useDirectorRuntimeStatus = () => useRuntimeStore((state) => ({
    status: state.directorStatus,
    error: state.directorError,
    setStatus: state.setDirectorStatus,
    setError: state.setDirectorError,
}));
/** 活跃 Worker 列表 */
export const useActiveWorkers = () => useRuntimeStore((state) => state.activeWorkers);
/** Worker 任务映射 */
export const useWorkerTasks = () => useRuntimeStore((state) => state.workerTasks);
/** 任务队列 */
export const useTaskQueue = () => useRuntimeStore((state) => ({
    pending: state.taskQueue,
    completed: state.completedTasks,
    addTask: state.addTask,
    removeTask: state.removeTask,
    completeTask: state.completeTask,
}));
