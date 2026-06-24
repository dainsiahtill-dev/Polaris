import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ResidentWorkspace } from './ResidentWorkspace';

const mockResidentState = {
  workspace: 'X:/Git/polaris',
  status: null,
  goals: [
    {
      goal_id: 'goal-approved',
      title: 'Stabilize PM contract quality',
      goal_type: 'reliability',
      source: 'manual',
      status: 'approved',
      motivation: 'Reduce drift in PM output',
      updated_at: '2026-03-07T00:00:00Z',
      evidence_refs: ['docs/resident/resident-engineering-rfc.md'],
      scope: ['src/backend/app/orchestration'],
    },
    {
      goal_id: 'goal-pending',
      title: 'Investigate flaky retries',
      goal_type: 'reliability',
      source: 'auto',
      status: 'pending',
      motivation: 'Retry storms are noisy',
      updated_at: '2026-03-07T00:00:00Z',
      evidence_refs: [],
      scope: [],
    },
  ],
  decisions: [
    {
      decision_id: 'decision-1',
      actor: 'pm',
      stage: 'goal_staging',
      summary: 'Selected bounded decomposition strategy',
      timestamp: '2026-03-07T00:00:00Z',
      run_id: 'resident-run-001',
      task_id: 'TASK-1',
      goal_id: 'goal-approved',
      verdict: 'success',
      strategy_tags: ['task_split', 'pm_bridge'],
      confidence: 0.92,
      context_refs: ['runtime/contexts/abc123'],
      options: [
        {
          option_id: 'opt-a',
          label: 'bounded decomposition',
          rationale: 'Lower regression risk',
          estimated_score: 0.91,
        },
      ],
      selected_option_id: 'opt-a',
      evidence_refs: ['runtime/contracts/plan.md'],
      evidence_bundle_id: 'bundle-1',
      affected_files: ['src/backend/polaris/cells/resident/autonomy/internal/resident_runtime_service.py'],
      affected_symbols: ['record_decision'],
      actual_outcome: {
        decision_source: 'resident_agi_supervisor',
        evidence_schema: 'resident.decision_event.v1',
        execution_profile_schema: 'task.execution_profile.v1',
        validator_result: 'validation_passed',
        promoted_to_pm_runtime: true,
        task_count: 2,
      },
    },
  ],
  loading: false,
  actionKey: '',
  error: null,
  residentRuntime: {
    active: true,
    mode: 'propose',
    tick_count: 3,
    last_tick_at: '2026-03-07T00:00:00Z',
  },
  residentIdentity: {
    name: 'Resident AGI Supervisor',
    mission: 'Supervise unattended Polaris development runs with governed evidence.',
    owner: 'human',
    operating_mode: 'propose',
  },
  residentAgenda: {
    current_focus: ['stabilize orchestration'],
    risk_register: ['goal backlog rising'],
    next_actions: ['approve reliability goal'],
    pending_goal_ids: [],
    approved_goal_ids: ['goal-approved'],
    materialized_goal_ids: [],
    active_experiment_ids: [],
    active_improvement_ids: [],
  },
  residentCounts: {
    goals: 1,
    decisions: 1,
    experiments: 1,
    improvements: 1,
  },
  residentInsights: [
    {
      insight_id: 'insight-1',
      summary: 'Prefer bounded decomposition',
      insight_type: 'meta_cognition',
      strategy_tag: 'task_split',
      confidence: 0.88,
      recommendation: 'Use narrower task scopes for risky runs.',
    },
  ],
  residentSkills: [],
  residentExperiments: [],
  residentImprovements: [],
  residentCapabilityGraph: {
    generated_at: '2026-03-07T00:00:00Z',
    capabilities: [
      {
        capability_id: 'cap-1',
        name: 'Task decomposition',
        kind: 'reasoning',
        score: 0.86,
        success_rate: 0.83,
        attempts: 6,
        evidence_count: 4,
      },
    ],
    gaps: ['shadow-runtime-promotion'],
  },
  residentAgiCapabilitySurface: {
    schema_version: 'resident.agi_capability_surface.v1',
    role_id: 'resident_agi',
    runtime_foundation: 'RoleRuntime / ContextOS / TurnEngine',
    implementation_cell: 'resident.autonomy',
    count: 3,
    items: [
      {
        capability_id: 'contextos.final_request_audit.read',
        name: 'Final provider-request audit',
        category: 'llm_audit',
        access: 'read_only',
        contract_ref: 'roles.final_request_context_audit',
      },
      {
        capability_id: 'run_ledger.read',
        name: 'Run Ledger projection',
        category: 'run_ledger',
        access: 'read_only',
        contract_ref: 'control_plane.run_ledger',
      },
      {
        capability_id: 'resident.goal_bridge.execute',
        name: 'Resident governed goal bridge',
        category: 'controlled_execution',
        access: 'execute_through_pm_ce_director_chain',
        contract_ref: 'resident.goal_bridge',
        risk_level: 'high',
      },
    ],
  },
  refresh: vi.fn(),
  isActing: vi.fn(() => false),
  start: vi.fn(),
  stop: vi.fn(),
  tick: vi.fn(),
  saveIdentity: vi.fn(),
  createGoal: vi.fn(async () => ({ goal_id: 'goal-new' })),
  approveGoal: vi.fn(async () => null),
  rejectGoal: vi.fn(async () => null),
  materializeGoal: vi.fn(async () => null),
  stageGoal: vi.fn(async () => null),
  runGoal: vi.fn(async () => null),
  extractSkills: vi.fn(async () => null),
  runExperiments: vi.fn(async () => null),
  runImprovements: vi.fn(async () => null),
};

vi.mock('@/hooks/useResident', () => ({
  useResident: () => mockResidentState,
}));

describe('ResidentWorkspace', () => {
  beforeEach(() => {
    Object.values(mockResidentState)
      .filter((value) => typeof value === 'function' && 'mockClear' in value)
      .forEach((fn) => (fn as ReturnType<typeof vi.fn>).mockClear());
  });

  it('renders the AGI workspace shell', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    expect(screen.getByText('AGI 工作区')).toBeInTheDocument();
    expect(screen.getByText('Resident AGI Supervisor')).toBeInTheDocument();
    expect(screen.getByText('最新元认知')).toBeInTheDocument();
    expect(screen.getByText('Task decomposition')).toBeInTheDocument();
    expect(screen.getByText('AGI Role 能力面')).toBeInTheDocument();
    expect(screen.getByText('Final provider-request audit')).toBeInTheDocument();
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('能力治理矩阵');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('PM → Chief Engineer → Director');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('Governed ops');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('High risk');
    expect(screen.getByTestId('resident-agi-governance-tags')).toHaveTextContent('resident.goal_bridge');
  });

  it('creates a goal from the AGI console', async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    expect(screen.getByText('目标生成台')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('目标标题'), { target: { value: 'Tighten Director retries' } });
    fireEvent.change(screen.getByLabelText('目标描述'), {
      target: { value: 'Retry storms are causing noise.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /创建 AGI 目标/i }));

    await waitFor(() => {
      expect(mockResidentState.createGoal).toHaveBeenCalledTimes(1);
    });
    expect(mockResidentState.createGoal).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Tighten Director retries' }),
    );
  });

  it('governs and runs approved goals', async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText('Stabilize PM contract quality'));
    expect(screen.getByRole('button', { name: '暂存' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂存' }));
    fireEvent.click(screen.getByRole('button', { name: '写入 PM' }));
    fireEvent.click(screen.getByRole('button', { name: '交给 PM' }));

    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(1, 'goal-approved', false);
    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(2, 'goal-approved', true);
    expect(mockResidentState.runGoal).toHaveBeenCalledWith('goal-approved', false, 1);
  });

  it('triggers a reflection tick from the header', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId('resident-tick'));
    expect(mockResidentState.tick).toHaveBeenCalledTimes(1);
  });

  it('surfaces evolution lab actions', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId('resident-tab-evolution'));
    expect(screen.getByText(/技能工坊/)).toBeInTheDocument();
    expect(screen.getByText(/反事实实验/)).toBeInTheDocument();
    expect(screen.getByText(/自改提案/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('resident-extract-skills'));
    fireEvent.click(screen.getByTestId('resident-run-experiments'));
    fireEvent.click(screen.getByTestId('resident-run-improvements'));

    expect(mockResidentState.extractSkills).toHaveBeenCalledTimes(1);
    expect(mockResidentState.runExperiments).toHaveBeenCalledTimes(1);
    expect(mockResidentState.runImprovements).toHaveBeenCalledTimes(1);
  });

  it('surfaces the AGI decision audit timeline', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="decisions"
      />,
    );

    expect(screen.getByText('决策审计面')).toBeInTheDocument();
    expect(screen.getByText('source of truth: decision_trace.jsonl')).toBeInTheDocument();
    expect(screen.getByText('resident.decision_event.v1')).toBeInTheDocument();
    expect(screen.getByText('PM → Chief Engineer → Director')).toBeInTheDocument();
    expect(screen.getByText('validation_passed')).toBeInTheDocument();
    expect(screen.getByText('bounded decomposition')).toBeInTheDocument();
    expect(screen.getByText('score 91%')).toBeInTheDocument();
    expect(screen.getByText(/evidence refs: runtime\/contracts\/plan.md/)).toBeInTheDocument();
    expect(screen.getByText(/symbols: record_decision/)).toBeInTheDocument();
  });

  it('rejects a pending goal', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText('Investigate flaky retries'));
    fireEvent.click(screen.getByTestId('resident-reject-goal'));
    expect(mockResidentState.rejectGoal).toHaveBeenCalledWith('goal-pending');
  });

  it('materializes an approved goal', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    fireEvent.click(screen.getByText('Stabilize PM contract quality'));
    fireEvent.click(screen.getByTestId('resident-materialize-goal'));
    expect(mockResidentState.materializeGoal).toHaveBeenCalledWith('goal-approved');
  });

  it('edits the AGI identity', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    fireEvent.click(screen.getByTestId('resident-edit-identity'));
    fireEvent.change(screen.getByLabelText('AGI 名称'), { target: { value: 'Polaris Resident' } });
    fireEvent.change(screen.getByLabelText('AGI 任务宣言'), { target: { value: 'Keep main green' } });
    fireEvent.click(screen.getByTestId('resident-save-identity'));
    expect(mockResidentState.saveIdentity).toHaveBeenCalledWith({
      name: 'Polaris Resident',
      mission: 'Keep main green',
    });
  });
});
