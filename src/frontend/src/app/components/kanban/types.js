/**
 * Kanban Board Types
 * Linear/Jira 风格看板视图的核心类型定义
 */
/** 列配置 */
export const COLUMN_CONFIG = {
    backlog: {
        title: 'Backlog',
        titleZh: '待办事项',
        color: 'border-slate-500/50',
    },
    todo: {
        title: 'To Do',
        titleZh: '计划中',
        color: 'border-blue-500/50',
    },
    in_progress: {
        title: 'In Progress',
        titleZh: '进行中',
        color: 'border-amber-500/50',
    },
    done: {
        title: 'Done',
        titleZh: '已完成',
        color: 'border-emerald-500/50',
    },
};
/** 所有列的默认顺序 */
export const COLUMN_ORDER = ['backlog', 'todo', 'in_progress', 'done'];
