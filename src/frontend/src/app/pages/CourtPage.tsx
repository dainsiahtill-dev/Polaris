/**
 * 宫廷投影页面
 *
 * 展示宫廷化3D UI投影的完整功能
 */

import React from 'react';
import { CourtContainer } from '../components/court';
import { devLogger } from '@/app/utils/devLogger';

export function CourtPage() {
  return (
    <div className="w-full h-screen bg-slate-950">
      <CourtContainer
        defaultCameraMode="overview"
        enableRealtime={true}
        onActorSelect={(actor) => {
          if (actor) {
            devLogger.debug('Selected actor:', actor);
          }
        }}
      />
    </div>
  );
}

export default CourtPage;
