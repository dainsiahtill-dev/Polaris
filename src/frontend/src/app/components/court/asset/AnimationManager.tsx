/**
 * 宫廷角色动画管理器
 *
 * 管理角色动画状态机，支持：
 * - 动画片段混合
 * - 状态到动画的映射
 * - 平滑过渡
 * - 循环控制
 */

import { useRef, useEffect, useCallback, useState } from 'react';
import * as THREE from 'three';
import type { CourtAsset } from './AssetLoader';
import type { ActorStatus } from '../../../types/court';
import { devLogger } from '@/app/utils/devLogger';

// 动画状态映射配置
export const STATUS_ANIMATION_MAP: Record<ActorStatus, string> = {
  offline: 'idle',
  idle: 'idle',
  thinking: 'thinking',
  executing: 'working',
  dispatching: 'working',
  reviewing: 'reviewing',
  approving: 'reviewing',
  blocked: 'blocked',
  success: 'success',
  failed: 'failed',
};

// 动画过渡配置
interface TransitionConfig {
  duration: number;
  easing: (t: number) => number;
}

const DEFAULT_TRANSITION: TransitionConfig = {
  duration: 0.3,
  easing: (t) => t * t * (3 - 2 * t), // smoothstep
};

export interface AnimationState {
  currentAction: THREE.AnimationAction | null;
  previousAction: THREE.AnimationAction | null;
  mixer: THREE.AnimationMixer;
  isTransitioning: boolean;
}

/**
 * 创建动画管理器
 */
export function createAnimationManager(asset: CourtAsset): AnimationState {
  const mixer = new THREE.AnimationMixer(asset.scene);

  // 初始化所有动画为停止状态
  asset.animations.forEach((clip) => {
    const action = mixer.clipAction(clip);
    action.setEffectiveWeight(0);
    action.stop();
  });

  return {
    currentAction: null,
    previousAction: null,
    mixer,
    isTransitioning: false,
  };
}

/**
 * 切换动画状态
 */
export function transitionToAnimation(
  state: AnimationState,
  asset: CourtAsset,
  status: ActorStatus,
  config: TransitionConfig = DEFAULT_TRANSITION
): void {
  const animName = STATUS_ANIMATION_MAP[status];
  const clip = asset.animations.get(animName);

  if (!clip) {
    devLogger.warn(`Animation '${animName}' not found for status '${status}'`);
    return;
  }

  const newAction = state.mixer.clipAction(clip);

  // 如果当前动画就是目标动画，不做切换
  if (state.currentAction === newAction) {
    return;
  }

  state.previousAction = state.currentAction;
  state.currentAction = newAction;
  state.isTransitioning = true;

  // 配置新动画
  newAction
    .reset()
    .setEffectiveTimeScale(1)
    .setEffectiveWeight(0)
    .play();

  // 配置旧动画淡出
  if (state.previousAction) {
    state.previousAction.setEffectiveWeight(1);
  }

  // 执行过渡
  const startTime = performance.now();
  const duration = config.duration * 1000;

  const animate = () => {
    const now = performance.now();
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = config.easing(progress);

    // 新动画淡入
    newAction.setEffectiveWeight(eased);

    // 旧动画淡出
    if (state.previousAction) {
      state.previousAction.setEffectiveWeight(1 - eased);
    }

    if (progress < 1) {
      requestAnimationFrame(animate);
    } else {
      // 过渡完成
      state.isTransitioning = false;
      if (state.previousAction) {
        state.previousAction.stop();
      }
      newAction.setEffectiveWeight(1);
    }
  };

  requestAnimationFrame(animate);
}

/**
 * React Hook: 使用角色动画
 */
export function useRoleAnimation(
  asset: CourtAsset | null,
  status: ActorStatus,
  isVisible: boolean = true
): {
  mixer: THREE.AnimationMixer | null;
  currentAnimation: string | null;
  isTransitioning: boolean;
} {
  const stateRef = useRef<AnimationState | null>(null);
  const [currentAnimation, setCurrentAnimation] = useState<string | null>(null);
  const [isTransitioning, setIsTransitioning] = useState(false);

  // 初始化动画管理器
  useEffect(() => {
    if (!asset) {
      stateRef.current = null;
      return;
    }

    stateRef.current = createAnimationManager(asset);

    return () => {
      if (stateRef.current) {
        stateRef.current.mixer.stopAllAction();
        stateRef.current = null;
      }
    };
  }, [asset]);

  // 状态切换时更新动画
  useEffect(() => {
    if (!stateRef.current || !asset) return;

    const animName = STATUS_ANIMATION_MAP[status];
    setCurrentAnimation(animName);
    setIsTransitioning(true);

    transitionToAnimation(stateRef.current, asset, status, {
      duration: 0.3,
      easing: (t) => t * t * (3 - 2 * t),
    });

    // 过渡完成后更新状态 - 添加清理函数防止组件卸载后更新状态
    const timer = setTimeout(() => {
      setIsTransitioning(false);
    }, 300);

    return () => clearTimeout(timer);
  }, [asset, status]);

  // 可见性控制
  useEffect(() => {
    if (!stateRef.current) return;

    if (isVisible) {
      stateRef.current.mixer.timeScale = 1;
    } else {
      stateRef.current.mixer.timeScale = 0;
    }
  }, [isVisible]);

  return {
    mixer: stateRef.current?.mixer || null,
    currentAnimation,
    isTransitioning,
  };
}

/**
 * 更新动画混合器
 */
export function updateAnimationMixer(
  mixer: THREE.AnimationMixer,
  deltaTime: number
): void {
  mixer.update(deltaTime);
}

/**
 * 批量更新多个动画混合器
 */
export function updateAllMixers(
  mixers: Map<string, THREE.AnimationMixer>,
  deltaTime: number
): void {
  mixers.forEach((mixer) => {
    mixer.update(deltaTime);
  });
}

/**
 * 创建动画混合 Hook（用于在 Canvas 帧循环中调用）
 */
export function useAnimationFrame(
  mixers: Map<string, THREE.AnimationMixer>
): void {
  const lastTimeRef = useRef(performance.now());

  useEffect(() => {
    let animationId: number;

    const animate = () => {
      const now = performance.now();
      const deltaTime = (now - lastTimeRef.current) / 1000;
      lastTimeRef.current = now;

      // 限制最大 deltaTime 防止标签页切换后跳变
      const clampedDelta = Math.min(deltaTime, 0.1);

      updateAllMixers(mixers, clampedDelta);
      animationId = requestAnimationFrame(animate);
    };

    animationId = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(animationId);
    };
  }, [mixers]);
}

/**
 * 获取默认动画片段（用于占位模型）
 */
export function createDefaultAnimations(): Map<string, THREE.AnimationClip> {
  const animations = new Map<string, THREE.AnimationClip>();

  // 创建简单的默认动画
  const idleClip = new THREE.AnimationClip('idle', 2, []);
  const workingClip = new THREE.AnimationClip('working', 1, []);
  const thinkingClip = new THREE.AnimationClip('thinking', 1.5, []);
  const blockedClip = new THREE.AnimationClip('blocked', 0.5, []);
  const successClip = new THREE.AnimationClip('success', 1, []);
  const failedClip = new THREE.AnimationClip('failed', 1, []);
  const reviewingClip = new THREE.AnimationClip('reviewing', 2, []);

  animations.set('idle', idleClip);
  animations.set('working', workingClip);
  animations.set('thinking', thinkingClip);
  animations.set('blocked', blockedClip);
  animations.set('success', successClip);
  animations.set('failed', failedClip);
  animations.set('reviewing', reviewingClip);

  return animations;
}
