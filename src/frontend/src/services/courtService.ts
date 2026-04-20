/**
 * Court (宫廷投影) Service
 *
 * 封装所有宫廷系统相关的API调用
 */

import { apiGet } from './apiClient';
import type { ApiResult } from './api.types';
import type {
  CourtState,
  CourtTopologyResponse,
  CourtActorState,
  CourtSceneConfig,
  CourtMappingResponse,
} from './api.types';

export type {
  CourtState,
  CourtTopologyResponse,
  CourtActorState,
  CourtSceneConfig,
  CourtScenePhase,
  ActorStatus,
  RiskLevel,
} from './api.types';

// ============================================================================
// Court API
// ============================================================================

/**
 * 获取宫廷拓扑结构
 */
export async function getCourtTopology(): Promise<ApiResult<CourtTopologyResponse>> {
  return apiGet<CourtTopologyResponse>('/court/topology', 'Failed to fetch court topology');
}

/**
 * 获取宫廷当前状态
 */
export async function getCourtState(): Promise<ApiResult<CourtState>> {
  return apiGet<CourtState>('/court/state', 'Failed to fetch court state');
}

/**
 * 获取角色详情
 */
export async function getActorDetail(roleId: string): Promise<ApiResult<CourtActorState>> {
  return apiGet<CourtActorState>(`/court/actors/${roleId}`, 'Failed to fetch actor detail');
}

/**
 * 获取场景配置
 */
export async function getSceneConfig(sceneId: string): Promise<ApiResult<CourtSceneConfig>> {
  return apiGet<CourtSceneConfig>(`/court/scenes/${sceneId}`, 'Failed to fetch scene config');
}

/**
 * 获取角色映射表
 */
export async function getRoleMapping(): Promise<ApiResult<CourtMappingResponse>> {
  return apiGet<CourtMappingResponse>('/court/mapping', 'Failed to fetch role mapping');
}
