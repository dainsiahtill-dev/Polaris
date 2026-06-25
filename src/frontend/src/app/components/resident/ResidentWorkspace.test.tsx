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
  residentRuntimeEvidence: {
    schema_version: 'resident.runtime_projection_evidence.v1',
    realtime_channel: 'runtime.v2.status.snapshot',
    projection_field: 'snapshot.resident',
    live_snapshot_available: true,
    http_details_loaded: true,
    source: 'runtime.v2_snapshot+http_details',
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
    decision_boundary_schema: 'resident.agi_decision_boundary.v1',
    authority_matrix_schema: 'resident.agi_authority_matrix.v1',
    role_id: 'resident_agi',
    runtime_foundation: 'RoleRuntime / ContextOS / TurnEngine',
    implementation_cell: 'resident.autonomy',
    count: 3,
    items: [
      {
        capability_id: 'resident.agi_decision_turn.execute',
        name: 'Resident AGI role decision turn',
        category: 'role_runtime',
        access: 'execute_through_role_runtime',
        contract_ref: 'resident.agi_decision_turn',
        endpoint: '/v2/resident/agi/decide',
        risk_level: 'medium',
        guardrails: ['AGI decisions must use the resident_agi role adapter, never a sidecar runtime.'],
        evidence_refs: ['resident_agi role_result'],
      },
      {
        capability_id: 'roles.registry.read',
        name: 'Canonical role registry',
        category: 'role_runtime',
        access: 'read_only',
        contract_ref: 'roles.registry',
      },
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
        guardrails: ['No shortcut from PM directly to Director.'],
        evidence_refs: ['PM runtime contract'],
      },
    ],
    decision_boundaries: [
      {
        boundary_id: 'role.runtime.foundation',
        name: 'Shared role runtime foundation',
        authority: 'platform_hard_rule',
        platform_hard_rule: 'Resident AGI uses the same RoleRuntime, ContextOS, and TurnEngine.',
        agi_decision_scope: 'Every AGI turn remains observable as resident_agi.',
        evidence_required: ['resident_agi role_result'],
      },
      {
        boundary_id: 'platform.invariants',
        name: 'Platform hard invariants',
        authority: 'platform_hard_rule',
        platform_hard_rule: 'Security, runtime topology, and final request audit are enforced by code.',
        agi_decision_scope: 'AGI may detect missing evidence and propose remediation.',
        evidence_required: ['final_request_context_audit', 'runtime.v2 events'],
      },
      {
        boundary_id: 'architecture.options',
        name: 'Architecture and dependency choice',
        authority: 'agi_recommendation',
        platform_hard_rule: 'Preserve Cell/KernelOne reuse and role handoff contracts.',
        agi_decision_scope: 'AGI may compare architecture options and library choices using task evidence.',
        evidence_required: ['task.execution_profile.v1', 'chief_engineer.blueprint'],
      },
      {
        boundary_id: 'goal.execution',
        name: 'Goal promotion and unattended execution',
        authority: 'agi_governed_execution',
        platform_hard_rule: 'Approved goals may only execute through the governed role chain.',
        agi_decision_scope: 'AGI may prioritize, stage, and promote evidence-backed goals.',
        evidence_required: ['decision_trace.jsonl', 'PM runtime contract'],
      },
    ],
    authority_matrix: {
      schema_version: 'resident.agi_authority_matrix.v1',
      runtime_foundation: 'roles.runtime + ContextOS + TurnEngine',
      role_id: 'resident_agi',
      chain: 'PM → Chief Engineer → Director',
      chain_required: true,
      platform_enforced: true,
      llm_decision_required: true,
      platform_hard_rules: ['role.runtime.foundation', 'platform.invariants'],
      agi_recommendation_boundaries: ['architecture.options', 'quality.response'],
      governed_execution_boundaries: ['goal.execution'],
      read_only_capabilities: ['roles.registry.read', 'contextos.final_request_audit.read', 'run_ledger.read'],
      governed_operation_capabilities: ['resident.agi_decision_turn.execute', 'resident.goal_bridge.execute'],
      high_risk_capabilities: ['resident.goal_bridge.execute'],
      canonical_contracts: ['resident.agi_decision_turn', 'resident.goal_bridge', 'roles.runtime'],
      counts: {
        platform_hard_rules: 2,
        agi_recommendations: 2,
        governed_execution_boundaries: 1,
        read_only_capabilities: 3,
        governed_operation_capabilities: 2,
        high_risk_capabilities: 1,
        canonical_contracts: 3,
      },
      decision_policy: {
        hard_rules: 'platform_enforced_non_overridable',
        governed_execution: 'canonical_role_chain_only',
        code_changes: 'director_authorized_tools_only',
      },
    },
  },
  residentAgiAuditPack: {
    schema_version: 'resident.agi_audit_pack.v1',
    workspace: 'X:/Git/polaris',
    role_id: 'resident_agi',
    runtime_foundation: 'roles.runtime + ContextOS + TurnEngine',
    truth_sources: [
      'resident.status',
      'resident.agi_capability_surface',
      'resident.decision_trace',
      'roles.registry',
    ],
    role_registry: {
      schema_version: 'resident.agi_role_registry.v1',
      dialogue_roles: ['pm', 'chief_engineer', 'director', 'qa', 'resident_agi'],
      adapter_roles: ['pm', 'chief_engineer', 'director', 'qa', 'resident_agi'],
      required_roles: ['pm', 'chief_engineer', 'director', 'qa', 'resident_agi'],
      missing_required_roles: [],
      resident_agi_available: true,
    },
    boundary_summary: {
      schema: 'resident.agi_decision_boundary.v1',
      boundary_ids: ['role.runtime.foundation', 'platform.invariants'],
    },
    authority_matrix: {
      schema_version: 'resident.agi_authority_matrix.v1',
      runtime_foundation: 'roles.runtime + ContextOS + TurnEngine',
      role_id: 'resident_agi',
      chain: 'PM → Chief Engineer → Director',
      chain_required: true,
      platform_enforced: true,
      llm_decision_required: true,
      counts: {
        platform_hard_rules: 2,
        agi_recommendations: 2,
        governed_execution_boundaries: 1,
        read_only_capabilities: 3,
        governed_operation_capabilities: 2,
        high_risk_capabilities: 1,
        canonical_contracts: 3,
      },
      decision_policy: {
        hard_rules: 'platform_enforced_non_overridable',
        governed_execution: 'canonical_role_chain_only',
        code_changes: 'director_authorized_tools_only',
      },
    },
    hard_rule_gate: {
      schema_version: 'resident.agi_hard_rule_gate.v1',
      status: 'pass',
      checks: [
        {
          check_id: 'role_registry.resident_agi_available',
          passed: true,
          detail: 'resident_agi must exist in dialogue and adapter registries.',
        },
        {
          check_id: 'topology.pm_ce_director_preserved',
          passed: true,
          detail: 'Downstream execution must preserve PM → Chief Engineer → Director.',
        },
      ],
      failed_check_ids: [],
      platform_enforced: true,
      llm_override_allowed: false,
    },
    run_ledger_summary: {
      schema_version: 'resident.agi_run_ledger_summary.v1',
      source: 'run_ledger_projection',
      available: false,
      ok: false,
      status: 'pending',
      projected: 0,
      total: 0,
      failed: 0,
      missing: 0,
      detail: 'run ledger projection is not available yet',
    },
    evidence_gate: {
      schema_version: 'resident.agi_evidence_gate.v1',
      status: 'hold',
      recommended_verdict: 'request_evidence',
      reason: 'Run Ledger projection is not available yet.',
      run_ledger_available: false,
      run_ledger_ok: false,
      context_snapshot_ref_count: 1,
      platform_enforced: false,
      llm_decision_required: true,
    },
    capability_surface: {
      schema_version: 'resident.agi_capability_surface.v1',
      items: [
        {
          capability_id: 'resident.agi_decision_turn.execute',
          name: 'Resident AGI role decision turn',
        },
      ],
    },
    recent_decisions: [
      {
        decision_id: 'decision-1',
        actor: 'pm',
        stage: 'goal_staging',
        summary: 'Selected bounded decomposition strategy',
      },
    ],
    evidence_refs: ['runtime/contracts/plan.md'],
    execution_constraints: [
      'AGI decisions must execute as resident_agi role turns.',
      'Downstream work must preserve PM → Chief Engineer → Director.',
    ],
    decision_endpoint: '/v2/resident/agi/decide',
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
  runAgiDecision: vi.fn(async () => null),
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
    expect(screen.getByTestId('resident-runtime-evidence')).toHaveTextContent(
      'resident.runtime_projection_evidence.v1',
    );
    expect(screen.getByTestId('resident-runtime-evidence')).toHaveTextContent('runtime.v2.status.snapshot');
    expect(screen.getByTestId('resident-runtime-evidence')).toHaveTextContent('snapshot.resident');
    expect(screen.getByTestId('resident-runtime-evidence')).toHaveTextContent('runtime.v2_snapshot+http_details');
    expect(screen.getByText('最新元认知')).toBeInTheDocument();
    expect(screen.getByText('Task decomposition')).toBeInTheDocument();
    expect(screen.getByText('AGI Role 能力面')).toBeInTheDocument();
    expect(screen.getByText('Resident AGI role decision turn')).toBeInTheDocument();
    expect(screen.getByText('Canonical role registry')).toBeInTheDocument();
    expect(screen.getByText('Final provider-request audit')).toBeInTheDocument();
    expect(screen.getByTestId('resident-agi-role-foundation')).toHaveTextContent('resident_agi');
    expect(screen.getByTestId('resident-agi-role-foundation')).toHaveTextContent('RoleRuntime / ContextOS / TurnEngine');
    expect(screen.getByTestId('resident-agi-role-foundation')).toHaveTextContent('canonical contract');
    expect(screen.getByTestId('resident-agi-role-foundation')).toHaveTextContent('PM → Chief Engineer → Director');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('能力治理矩阵');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('PM → Chief Engineer → Director');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('Governed ops');
    expect(screen.getByTestId('resident-agi-governance-matrix')).toHaveTextContent('High risk');
    expect(screen.getByTestId('resident-agi-authority-matrix')).toHaveTextContent('resident.agi_authority_matrix.v1');
    expect(screen.getByTestId('resident-agi-authority-matrix')).toHaveTextContent('hard rules 2');
    expect(screen.getByTestId('resident-agi-authority-matrix')).toHaveTextContent('AGI judgement 2');
    expect(screen.getByTestId('resident-agi-governance-tags')).toHaveTextContent('resident.goal_bridge');
    expect(screen.getByTestId('resident-agi-governance-tags')).toHaveTextContent('canonical_role_chain_only');
    expect(screen.getByTestId('resident-agi-governance-tags')).toHaveTextContent('platform_enforced_non_overridable');
    expect(screen.getByText('risk high')).toBeInTheDocument();
    expect(screen.getByText('No shortcut from PM directly to Director.')).toBeInTheDocument();
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('AGI 决策边界');
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('平台硬规则');
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('AGI 智能判断');
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('AGI 受控执行');
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('Architecture and dependency choice');
    expect(screen.getByTestId('resident-agi-decision-boundaries')).toHaveTextContent('final_request_context_audit');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('AGI 审计包');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('resident.agi_audit_pack.v1');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('Hard gate pass');
    expect(screen.getByTestId('resident-agi-audit-authority-matrix')).toHaveTextContent(
      'resident.agi_authority_matrix.v1',
    );
    expect(screen.getByTestId('resident-agi-audit-authority-matrix')).toHaveTextContent('governed ops 2');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('hold → request_evidence');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('Run Ledger pending');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('llm override: blocked');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('resident.agi_decision_turn.execute');
    expect(screen.getByTestId('resident-agi-audit-pack')).toHaveTextContent('PM → Chief Engineer → Director');
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

  it('runs a governed AGI decision turn from the decisions tab', () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/polaris"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="decisions"
      />,
    );

    expect(screen.getByTestId('resident-agi-decision-turn')).toHaveTextContent('AGI 决策回合');
    fireEvent.change(screen.getByLabelText('AGI 决策目标'), {
      target: { value: 'Decide whether the current run can proceed.' },
    });
    fireEvent.click(screen.getByTestId('resident-run-agi-decision'));

    expect(mockResidentState.runAgiDecision).toHaveBeenCalledWith(
      expect.objectContaining({
        decision_type: 'platform_supervision',
        objective: 'Decide whether the current run can proceed.',
        candidate_actions: ['continue', 'block', 'request_evidence', 'escalate'],
        evidence_refs: ['runtime/contracts/plan.md'],
        include_audit_pack: true,
        audit_pack_decision_limit: 12,
        evidence: expect.objectContaining({
          resident_agi_audit_pack_loaded: true,
          resident_agi_audit_pack_schema: 'resident.agi_audit_pack.v1',
          resident_agi_available: true,
          resident_agi_hard_rule_gate_status: 'pass',
          resident_agi_evidence_gate_status: 'hold',
          resident_agi_evidence_gate_recommended_verdict: 'request_evidence',
          resident_agi_authority_matrix_schema: 'resident.agi_authority_matrix.v1',
          resident_agi_chain_required: true,
        }),
      }),
    );
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
