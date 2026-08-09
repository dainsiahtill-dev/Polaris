import { useReducer, useCallback } from 'react';
const DEFAULT_JSON_LOG_PATH = 'runtime/events/pm.events.jsonl';
const normalizeJsonLogPath = (value) => {
    const raw = String(value ?? '').trim().replace(/\\/g, '/');
    if (!raw)
        return DEFAULT_JSON_LOG_PATH;
    if (raw === '.polaris/runtime')
        return 'runtime';
    if (raw.startsWith('.polaris/runtime/')) {
        return `runtime/${raw.slice('.polaris/runtime/'.length)}`;
    }
    return raw;
};
const defaultForm = {
    promptProfile: 'zhenguan_governance',
    refreshInterval: 3,
    autoRefresh: true,
    pmInterval: 20,
    pmTimeout: 0,
    pmRunsDirector: true,
    pmDirectorShowOutput: true,
    pmDirectorTimeout: 600,
    pmDirectorIterations: 1,
    pmDirectorMatchMode: 'latest',
    pmShowOutput: true,
    pmMaxFailures: 5,
    pmMaxBlocked: 5,
    pmMaxSame: 3,
    directorIterations: 1,
    directorExecutionMode: 'parallel',
    directorMaxParallelTasks: 3,
    directorReadyTimeoutSeconds: 30,
    directorClaimTimeoutSeconds: 30,
    directorPhaseTimeoutSeconds: 900,
    directorCompleteTimeoutSeconds: 30,
    directorTaskTimeoutSeconds: 3600,
    directorForever: false,
    directorShowOutput: true,
    slmEnabled: false,
    qaEnabled: true,
    ramdiskRoot: '',
    jsonLogPath: DEFAULT_JSON_LOG_PATH,
    showMemory: false,
    debugTracing: false,
    ioFsyncMode: 'strict',
    memoryRefsMode: 'soft',
};
function formReducer(state, action) {
    switch (action.type) {
        case 'SET_FIELD':
            return { ...state, [action.field]: action.value };
        case 'SET_MULTIPLE':
            return { ...state, ...action.payload };
        case 'RESET':
            return defaultForm;
        case 'LOAD':
            return { ...state, ...action.payload };
        default:
            return state;
    }
}
export function useGeneralSettingsForm(options = {}) {
    const { initialSettings } = options;
    const [form, dispatch] = useReducer(formReducer, {
        ...defaultForm,
        ...initialSettings,
    });
    const setField = useCallback((field, value) => {
        dispatch({ type: 'SET_FIELD', field, value });
    }, []);
    const setMultiple = useCallback((payload) => {
        dispatch({ type: 'SET_MULTIPLE', payload });
    }, []);
    const reset = useCallback(() => {
        dispatch({ type: 'RESET' });
    }, []);
    const load = useCallback((settings) => {
        dispatch({ type: 'LOAD', payload: settings });
    }, []);
    const toPayload = useCallback(() => ({
        prompt_profile: form.promptProfile,
        interval: form.pmInterval,
        timeout: form.pmTimeout,
        refresh_interval: form.refreshInterval,
        auto_refresh: form.autoRefresh,
        show_memory: form.showMemory,
        io_fsync_mode: form.ioFsyncMode,
        memory_refs_mode: form.memoryRefsMode,
        ramdisk_root: form.ramdiskRoot || undefined,
        json_log_path: normalizeJsonLogPath(form.jsonLogPath),
        pm_show_output: form.pmShowOutput,
        pm_runs_director: form.pmRunsDirector,
        pm_director_show_output: form.pmDirectorShowOutput,
        pm_director_timeout: form.pmDirectorTimeout,
        pm_director_iterations: form.pmDirectorIterations,
        pm_director_match_mode: form.pmDirectorMatchMode,
        pm_max_failures: form.pmMaxFailures,
        pm_max_blocked: form.pmMaxBlocked,
        pm_max_same: form.pmMaxSame,
        director_iterations: form.directorIterations,
        director_execution_mode: form.directorExecutionMode,
        director_max_parallel_tasks: form.directorMaxParallelTasks,
        director_ready_timeout_seconds: form.directorReadyTimeoutSeconds,
        director_claim_timeout_seconds: form.directorClaimTimeoutSeconds,
        director_phase_timeout_seconds: form.directorPhaseTimeoutSeconds,
        director_complete_timeout_seconds: form.directorCompleteTimeoutSeconds,
        director_task_timeout_seconds: form.directorTaskTimeoutSeconds,
        director_forever: form.directorForever,
        director_show_output: form.directorShowOutput,
        slm_enabled: form.slmEnabled,
        qa_enabled: form.qaEnabled,
        debug_tracing: form.debugTracing,
    }), [form]);
    return {
        form,
        setField,
        setMultiple,
        reset,
        load,
        toPayload,
    };
}
export function mapSettingsToForm(settings) {
    return {
        promptProfile: String(settings.prompt_profile || 'zhenguan_governance'),
        refreshInterval: Number(settings.refresh_interval ?? 3),
        autoRefresh: Boolean(settings.auto_refresh ?? true),
        pmInterval: Number(settings.interval ?? 20),
        pmTimeout: Number(settings.timeout ?? 0),
        pmShowOutput: Boolean(settings.pm_show_output ?? true),
        pmRunsDirector: Boolean(settings.pm_runs_director ?? true),
        pmDirectorShowOutput: Boolean(settings.pm_director_show_output ?? true),
        pmDirectorTimeout: Number(settings.pm_director_timeout ?? 600),
        pmDirectorIterations: Number(settings.pm_director_iterations ?? 1),
        pmDirectorMatchMode: String(settings.pm_director_match_mode ?? 'latest'),
        pmMaxFailures: Number(settings.pm_max_failures ?? 5),
        pmMaxBlocked: Number(settings.pm_max_blocked ?? 5),
        pmMaxSame: Number(settings.pm_max_same ?? 3),
        directorIterations: Number(settings.director_iterations ?? 1),
        directorExecutionMode: (settings.director_execution_mode === 'serial' ? 'serial' : 'parallel'),
        directorMaxParallelTasks: Number(settings.director_max_parallel_tasks ?? 3),
        directorReadyTimeoutSeconds: Number(settings.director_ready_timeout_seconds ?? 30),
        directorClaimTimeoutSeconds: Number(settings.director_claim_timeout_seconds ?? 30),
        directorPhaseTimeoutSeconds: Number(settings.director_phase_timeout_seconds ?? 900),
        directorCompleteTimeoutSeconds: Number(settings.director_complete_timeout_seconds ?? 30),
        directorTaskTimeoutSeconds: Number(settings.director_task_timeout_seconds ?? 3600),
        directorForever: Boolean(settings.director_forever ?? false),
        directorShowOutput: Boolean(settings.director_show_output ?? true),
        slmEnabled: Boolean(settings.slm_enabled ?? false),
        qaEnabled: Boolean(settings.qa_enabled ?? true),
        ramdiskRoot: String(settings.ramdisk_root ?? ''),
        jsonLogPath: normalizeJsonLogPath(settings.json_log_path),
        showMemory: Boolean(settings.show_memory ?? false),
        debugTracing: Boolean(settings.debug_tracing ?? false),
        ioFsyncMode: (settings.io_fsync_mode === 'relaxed' ? 'relaxed' : 'strict'),
        memoryRefsMode: (settings.memory_refs_mode === 'strict' ? 'strict' :
            settings.memory_refs_mode === 'off' ? 'off' : 'soft'),
    };
}
