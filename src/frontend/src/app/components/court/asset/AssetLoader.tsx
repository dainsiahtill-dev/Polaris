/**
 * Court Asset Loader
 *
 * 支持 GLB/GLTF 格式角色资产加载，包含：
 * - LOD（细节层次）管理
 * - 动画片段缓存
 * - 资产压缩/解压
 * - 加载状态追踪
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import * as THREE from 'three';

// 资产类型定义
export interface CourtAsset {
  roleId: string;
  scene: THREE.Group;
  lodLevel: number;
  animations: Map<string, THREE.AnimationClip>;
  isCompressed: boolean;
}

// LOD 配置
export interface LODConfig {
  level: number;
  distance: number;
  url: string;
  geometryCount: number;
}

// 角色资产配置
export const ROLE_ASSET_CONFIG: Record<string, LODConfig[]> = {
  // User - Highest detail
  emperor: [
    { level: 0, distance: 0, url: '/assets/court/emperor_lod0.glb', geometryCount: 15000 },
    { level: 1, distance: 10, url: '/assets/court/emperor_lod1.glb', geometryCount: 5000 },
    { level: 2, distance: 20, url: '/assets/court/emperor_lod2.glb', geometryCount: 1000 },
  ],
  // Offices - Medium detail
  zhongshu_ling: [
    { level: 0, distance: 0, url: '/assets/court/official_lod0.glb', geometryCount: 8000 },
    { level: 1, distance: 15, url: '/assets/court/official_lod1.glb', geometryCount: 3000 },
    { level: 2, distance: 30, url: '/assets/court/official_lod2.glb', geometryCount: 800 },
  ],
  shangshu_ling: [
    { level: 0, distance: 0, url: '/assets/court/official_lod0.glb', geometryCount: 8000 },
    { level: 1, distance: 15, url: '/assets/court/official_lod1.glb', geometryCount: 3000 },
    { level: 2, distance: 30, url: '/assets/court/official_lod2.glb', geometryCount: 800 },
  ],
  menxia_shilang: [
    { level: 0, distance: 0, url: '/assets/court/official_lod0.glb', geometryCount: 8000 },
    { level: 1, distance: 15, url: '/assets/court/official_lod1.glb', geometryCount: 3000 },
    { level: 2, distance: 30, url: '/assets/court/official_lod2.glb', geometryCount: 800 },
  ],
  // 六部尚书 - 标准细节
  gongbu_shangshu: [
    { level: 0, distance: 0, url: '/assets/court/minister_lod0.glb', geometryCount: 6000 },
    { level: 1, distance: 12, url: '/assets/court/minister_lod1.glb', geometryCount: 2500 },
    { level: 2, distance: 25, url: '/assets/court/minister_lod2.glb', geometryCount: 600 },
  ],
  // 部属官员 - 低细节
  gongbu_officer_1: [
    { level: 0, distance: 0, url: '/assets/court/officer_lod0.glb', geometryCount: 4000 },
    { level: 1, distance: 10, url: '/assets/court/officer_lod1.glb', geometryCount: 1500 },
    { level: 2, distance: 20, url: '/assets/court/officer_lod2.glb', geometryCount: 400 },
  ],
};

// 默认配置（用于未配置的角色）
export const DEFAULT_LOD_CONFIG: LODConfig[] = [
  { level: 0, distance: 0, url: '/assets/court/default_lod0.glb', geometryCount: 3000 },
  { level: 1, distance: 15, url: '/assets/court/default_lod1.glb', geometryCount: 1000 },
  { level: 2, distance: 30, url: '/assets/court/default_lod2.glb', geometryCount: 300 },
];

// 资产加载状态
interface AssetLoadingState {
  isLoading: boolean;
  progress: number;
  error: Error | null;
  asset: CourtAsset | null;
}

/**
 * 模拟加载角色资产（实际实现需要GLTFLoader）
 */
export async function loadRoleAsset(
  roleId: string,
  lodLevel: number = 0
): Promise<CourtAsset> {
  // 创建默认空场景作为占位
  const scene = new THREE.Group();

  // 添加一个占位网格
  const geometry = new THREE.BoxGeometry(1, 1.5, 0.5);
  const material = new THREE.MeshStandardMaterial({ color: 0x888888 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.y = 0.75;
  scene.add(mesh);

  const asset: CourtAsset = {
    roleId,
    scene,
    lodLevel,
    animations: new Map(),
    isCompressed: false,
  };

  return asset;
}

/**
 * React Hook: 使用角色资产
 */
export function useRoleAsset(roleId: string, lodLevel: number = 0): AssetLoadingState {
  const [state, setState] = useState<AssetLoadingState>({
    isLoading: true,
    progress: 0,
    error: null,
    asset: null,
  });

  useEffect(() => {
    const loadAsset = async () => {
      try {
        setState(prev => ({ ...prev, isLoading: true, error: null }));
        const asset = await loadRoleAsset(roleId, lodLevel);
        setState({
          isLoading: false,
          progress: 100,
          error: null,
          asset,
        });
      } catch (error) {
        setState({
          isLoading: false,
          progress: 0,
          error: error instanceof Error ? error : new Error(String(error)),
          asset: null,
        });
      }
    };

    loadAsset();
  }, [roleId, lodLevel]);

  return state;
}

/**
 * React Hook: 使用多 LOD 资产
 */
export function useLODAssets(roleId: string): {
  assets: Map<number, CourtAsset>;
  currentLOD: number;
  setCurrentLOD: (level: number) => void;
  isLoading: boolean;
} {
  const [assets, setAssets] = useState<Map<number, CourtAsset>>(new Map());
  const [currentLOD, setCurrentLOD] = useState(0);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadedAssets = new Map<number, CourtAsset>();

    const loadAll = async () => {
      setIsLoading(true);

      // 加载 LOD0
      try {
        const lod0 = await loadRoleAsset(roleId, 0);
        loadedAssets.set(0, lod0);
        setAssets(new Map(loadedAssets));
        setIsLoading(false);
      } catch (error) {
        setIsLoading(false);
      }
    };

    loadAll();
  }, [roleId]);

  return { assets, currentLOD, setCurrentLOD, isLoading };
}

/**
 * 预加载所有资产
 */
export async function preloadAllAssets(
  onProgress?: (loaded: number, total: number) => void
): Promise<void> {
  const allRoles = Object.keys(ROLE_ASSET_CONFIG);
  const total = allRoles.length;
  let loaded = 0;

  await Promise.all(
    allRoles.map(async (roleId) => {
      try {
        await loadRoleAsset(roleId, 0);
        loaded++;
        onProgress?.(loaded, total);
      } catch {
        // 忽略错误
      }
    })
  );
}

/**
 * 清理资产内存
 */
export function disposeAsset(asset: CourtAsset): void {
  asset.scene.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      child.geometry.dispose();
      if (Array.isArray(child.material)) {
        child.material.forEach(m => m.dispose());
      } else {
        child.material.dispose();
      }
    }
  });
}
