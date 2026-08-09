/**
 * 宫廷投影系统类型定义
 *
 * 本模块定义了前端宫廷化 3D UI 投影所需的数据类型
 * 与后端 app/models/court.py 保持同步
 */
// 动画状态映射（用于3D模型）
export const ANIMATION_STATE_MAP = {
    offline: 'offline',
    idle: 'idle',
    thinking: 'thinking',
    executing: 'executing',
    dispatching: 'executing',
    reviewing: 'reviewing',
    approving: 'approving',
    blocked: 'blocked',
    success: 'success',
    failed: 'failed',
};
// 状态优先级（用于确定显示状态）
export const STATUS_PRIORITY = [
    'failed',
    'blocked',
    'executing',
    'dispatching',
    'thinking',
    'reviewing',
    'approving',
    'success',
    'idle',
    'offline',
];
// 状态颜色映射
export const STATUS_COLORS = {
    offline: '#666666',
    idle: '#44aa44',
    thinking: '#4488ff',
    executing: '#ffaa00',
    dispatching: '#ffaa00',
    reviewing: '#aa44ff',
    approving: '#aa44ff',
    blocked: '#ff4444',
    success: '#00ff00',
    failed: '#ff0000',
};
// 风险等级颜色映射
export const RISK_COLORS = {
    none: '#00ff00',
    low: '#88ff00',
    medium: '#ffff00',
    high: '#ff8800',
    critical: '#ff0000',
};
// 场景名称映射
export const SCENE_NAMES = {
    taiji_hall: 'Main Hall',
    zhongshu_pavilion: 'Architect Office',
    shangshu_hall: 'PM Office',
    gongbu_blueprint: 'Engineering Blueprint',
    construction_site: 'Construction Site',
    menxia_tower: 'QA Review Desk',
};
// 角色显示名称映射
export const ROLE_DISPLAY_NAMES = {
    emperor: 'User',
    zhongshu_ling: 'Architect',
    zhongshu_shilang: '中书侍郎',
    menxia_shilang: '门下侍郎',
    menxia_shizhong: 'QA',
    shangshu_ling: 'PM',
    libu_shangshu: 'HR',
    hubu_shangshu: 'CFO',
    libu_shangshu2: 'Protocol',
    bingbu_shangshu: 'Security',
    xingbu_shangshu: 'Compliance',
    gongbu_shangshu: 'Chief Engineer',
    libu_officer_1: 'HR Officer',
    libu_officer_2: 'HR Clerk',
    hubu_officer_1: 'FinOps Officer',
    hubu_officer_2: 'FinOps Clerk',
    libu2_officer_1: 'Protocol Officer',
    libu2_officer_2: 'Protocol Clerk',
    bingbu_officer_1: 'Security Officer',
    bingbu_officer_2: 'Security Clerk',
    xingbu_officer_1: 'Compliance Officer',
    xingbu_officer_2: 'Compliance Clerk',
    gongbu_officer_1: 'Engineering Officer',
    gongbu_officer_2: 'Engineering Clerk',
};
