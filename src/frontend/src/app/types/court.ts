/**
 * 宫廷投影系统类型定义
 *
 * 本模块定义了前端宫廷化 3D UI 投影所需的数据类型
 * 与后端 app/models/court.py 保持同步
 */

export type CourtRole =
  | 'emperor'
  | 'zhongshu_ling'
  | 'zhongshu_shilang'
  | 'menxia_shilang'
  | 'menxia_shizhong'
  | 'shangshu_ling'
  | 'libu_shangshu'
  | 'hubu_shangshu'
  | 'libu_shangshu2'
  | 'bingbu_shangshu'
  | 'xingbu_shangshu'
  | 'gongbu_shangshu'
  | 'libu_officer_1'
  | 'libu_officer_2'
  | 'hubu_officer_1'
  | 'hubu_officer_2'
  | 'libu2_officer_1'
  | 'libu2_officer_2'
  | 'bingbu_officer_1'
  | 'bingbu_officer_2'
  | 'xingbu_officer_1'
  | 'xingbu_officer_2'
  | 'gongbu_officer_1'
  | 'gongbu_officer_2';

export type CourtScenePhase =
  | 'court_audience'
  | 'draft'
  | 'decompose'
  | 'blueprint'
  | 'build'
  | 'review'
  | 'finalize';

export type ActorStatus =
  | 'offline'
  | 'idle'
  | 'thinking'
  | 'executing'
  | 'dispatching'
  | 'reviewing'
  | 'approving'
  | 'blocked'
  | 'success'
  | 'failed';

export type RiskLevel = 'none' | 'low' | 'medium' | 'high' | 'critical';

export interface CourtEvidenceRef {
  path: string;
  channel?: string;
  runId?: string;
  taskId?: string;
  eventId?: string;
}

export interface CourtActorState {
  role_id: string;
  role_name: string;
  status: ActorStatus;
  current_action: string;
  task_id?: string;
  risk_level: RiskLevel;
  evidence_refs: CourtEvidenceRef[];
  metadata: Record<string, unknown>;
  updated_at: number;
}

export interface CourtTopologyNode {
  role_id: string;
  role_name: string;
  parent_id?: string;
  position: [number, number, number];
  department: string;
  level: number;
  is_interactive: boolean;
}

export interface CourtActionEvent {
  action_type: string;
  from_role: string;
  to_role?: string;
  payload: Record<string, unknown>;
  ts: number;
  evidence_refs: CourtEvidenceRef[];
}

export interface CourtSceneConfig {
  scene_id: string;
  scene_name: string;
  phase: CourtScenePhase;
  description: string;
  camera_position: [number, number, number];
  focus_roles: string[];
  transitions: string[];
}

export interface CourtState {
  phase: CourtScenePhase;
  current_scene: string;
  actors: Record<string, CourtActorState>;
  topology?: CourtTopologyNode[];
  recent_events: CourtActionEvent[];
  updated_at: number;
}

export interface CourtTopologyResponse {
  nodes: CourtTopologyNode[];
  count: number;
  total: number;
  scenes: Record<string, CourtSceneConfig>;
}

// 动画状态映射（用于3D模型）
export const ANIMATION_STATE_MAP: Record<ActorStatus, string> = {
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
export const STATUS_PRIORITY: ActorStatus[] = [
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
export const STATUS_COLORS: Record<ActorStatus, string> = {
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
export const RISK_COLORS: Record<RiskLevel, string> = {
  none: '#00ff00',
  low: '#88ff00',
  medium: '#ffff00',
  high: '#ff8800',
  critical: '#ff0000',
};

// 场景名称映射
export const SCENE_NAMES: Record<string, string> = {
  taiji_hall: 'Main Hall',
  zhongshu_pavilion: 'Architect Office',
  shangshu_hall: 'PM Office',
  gongbu_blueprint: 'Engineering Blueprint',
  construction_site: 'Construction Site',
  menxia_tower: 'QA Review Desk',
};

// 角色显示名称映射
export const ROLE_DISPLAY_NAMES: Record<string, string> = {
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
