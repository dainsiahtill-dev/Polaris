/**
 * Court (宫廷投影) Service
 *
 * 封装所有宫廷系统相关的API调用
 */
import { apiGet } from './apiClient';
// ============================================================================
// Court API
// ============================================================================
/**
 * 获取宫廷拓扑结构
 */
export async function getCourtTopology() {
    return apiGet('/v2/court/topology', 'Failed to fetch court topology');
}
/**
 * 获取宫廷当前状态
 */
export async function getCourtState() {
    return apiGet('/v2/court/state', 'Failed to fetch court state');
}
/**
 * 获取角色详情
 */
export async function getActorDetail(roleId) {
    return apiGet(`/v2/court/actors/${roleId}`, 'Failed to fetch actor detail');
}
/**
 * 获取场景配置
 */
export async function getSceneConfig(sceneId) {
    return apiGet(`/v2/court/scenes/${sceneId}`, 'Failed to fetch scene config');
}
/**
 * 获取角色映射表
 */
export async function getRoleMapping() {
    return apiGet('/v2/court/mapping', 'Failed to fetch role mapping');
}
