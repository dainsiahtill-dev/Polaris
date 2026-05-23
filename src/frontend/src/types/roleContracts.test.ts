import { describe, expect, it } from 'vitest';
import {
  ROLE_TASK_STATUS_VALUES,
  type ChiefEngineerBlueprintListV1,
  type RoleTaskContractV1,
} from './roleContracts';
import type { UseRoleChatOptions } from '@/app/components/ai-dialogue/useRoleChat';
import type { RoleChatRole } from '@/services/api.types';
import type { DialogueRole } from '@/services/conversationApi';

describe('roleContracts', () => {
  it('keeps Director task fields shared with backend contract names', () => {
    const task: RoleTaskContractV1 = {
      id: 'PM-1',
      subject: 'Implement runtime diagnostics',
      description: 'Expose NATS/WS/rate limit state',
      status: 'RUNNING',
      priority: 'HIGH',
      claimed_by: 'director-1',
      result: null,
      metadata: { pm_task_id: 'PM-1' },
      goal: '诊断可排查',
      acceptance: ['shows nats', 'shows websocket'],
      target_files: ['src/backend/polaris/delivery/http/v2/runtime_diagnostics.py'],
      dependencies: [],
      current_file: null,
      error: null,
      worker: 'director-1',
      pm_task_id: 'PM-1',
      blueprint_id: 'bp-1',
      blueprint_path: 'runtime/blueprints/bp-1.json',
      runtime_blueprint_path: 'runtime/blueprints/bp-1.json',
    };

    expect(ROLE_TASK_STATUS_VALUES).toContain(task.status);
    expect(task.metadata.pm_task_id).toBe('PM-1');
    expect(task.runtime_blueprint_path).toContain('bp-1');
  });

  it('keeps Chief Engineer blueprint list shape explicit', () => {
    const listing: ChiefEngineerBlueprintListV1 = {
      total: 1,
      blueprints: [{
        blueprint_id: 'bp-1',
        title: 'Runtime Diagnostics',
        summary: 'NATS and rate limit panel',
        status: null,
        source: 'runtime/blueprints',
        target_files: ['src/frontend/src/app/components/RuntimeDiagnosticsWorkspace.tsx'],
        updated_at: null,
        raw: { blueprint_id: 'bp-1' },
      }],
    };

    expect(listing.blueprints[0].source).toBe('runtime/blueprints');
  });

  it('keeps Chief Engineer available to the shared dialogue stack', () => {
    const role: DialogueRole = 'chief_engineer';
    const apiRole: RoleChatRole = 'chief_engineer';
    const hookOptions: Pick<UseRoleChatOptions, 'role'> = {
      role: 'chief_engineer',
    };

    expect(role).toBe('chief_engineer');
    expect(apiRole).toBe('chief_engineer');
    expect(hookOptions.role).toBe('chief_engineer');
  });

  it('keeps Scout out of the backend role-chat prompt registry type', () => {
    const conversationOnlyRole: DialogueRole = 'scout';
    // @ts-expect-error Scout has no backend role-chat prompt template.
    const unsupportedRole: RoleChatRole = 'scout';

    expect(conversationOnlyRole).toBe('scout');
    expect(unsupportedRole).toBe('scout');
  });
});
