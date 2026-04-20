/**
 * 宫廷资产系统导出
 */

export {
  useRoleAsset,
  useLODAssets,
  loadRoleAsset,
  preloadAllAssets,
  disposeAsset,
  ROLE_ASSET_CONFIG,
  DEFAULT_LOD_CONFIG,
} from './AssetLoader';

export {
  useRoleAnimation,
  useAnimationFrame,
  createAnimationManager,
  transitionToAnimation,
  updateAnimationMixer,
  updateAllMixers,
  createDefaultAnimations,
  STATUS_ANIMATION_MAP,
} from './AnimationManager';

export {
  usePerformanceMonitor,
  useVisibilityCulling,
  calculateLODLevel,
  shouldRenderActor,
  PerformancePanel,
  LOD_LEVELS,
} from './PerformanceMonitor';

export type {
  CourtAsset,
  LODConfig,
} from './AssetLoader';

export type {
  PerformanceMetrics,
  LODSettings,
} from './PerformanceMonitor';
