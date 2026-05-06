/**
 * 任务状态枚举
 */
export enum TaskStatus {
    PENDING = 'pending',
    IN_PROGRESS = 'in_progress',
    COMPLETED = 'completed',
    FAILED = 'failed',
    BLOCKED = 'blocked',
    SUCCESS = 'success',
}

/**
 * 验收标准接口
 */
export interface AcceptanceCriteria {
    id?: string;
    description: string;
    status?: 'pending' | 'met' | 'failed';
}

/**
 * 预算信息接口
 */
export interface TaskBudget {
    used: number;
    total: number;
    unit: 'tokens' | 'requests' | 'time';
}

/**
 * PM 任务接口（严格类型版本）
 */
export interface PmTask {
    id: string;
    title: string;
    goal?: string;
    summary?: string;
    description?: string;
    status: TaskStatus;
    state?: string;
    done: boolean;
    completed?: boolean;
    priority: number;
    acceptance: AcceptanceCriteria[];
    acceptance_criteria?: string[];
    command?: string;
    execution_checklist?: string[];
    qa_contract?: Record<string, unknown>;
    scope_paths?: string[];
    target_files?: string[];
    // 可选扩展字段
    metadata?: Record<string, unknown>;
    budget?: TaskBudget;
    created_at?: string;
    createdAt?: string;
    started_at?: string;
    startedAt?: string;
    completed_at?: string;
    completedAt?: string;
    estimated_time?: number;
    estimatedTime?: number;
    dependencies?: string[];
    blocked_by?: string[];
    tags?: string[];
    files_modified?: number;
    retries?: number;
    retry_count?: number;
    output?: string;
    error?: string;
    assigned_to?: string;
    assignedTo?: string;
    assignee?: string;
    assignee_type?: string;
    assigned_worker?: string;
    worker_id?: string;
}

/**
 * 成功率统计接口
 */
export interface SuccessStats {
    successes: number | null;
    total: number | null;
    rate: number | null;
}

/**
 * PM 状态接口
 */
export interface PmState {
    completed_task_ids?: string[];
    completed_task_count?: number;
    last_director_task_id?: string;
    last_director_task_title?: string;
    last_director_status?: string;
    last_director_detail?: string;
    last_updated_ts?: string;
    pm_iteration?: number;
}

/**
 * 任务队列项接口
 */
export interface TaskQueueItem {
    key: string;
    title: string;
    id?: string;
    isCompleted: boolean;
    isCurrent: boolean;
}

/**
 * 进度模式类型
 */
export type ProgressMode = 'done' | 'position' | 'success' | 'idle';
