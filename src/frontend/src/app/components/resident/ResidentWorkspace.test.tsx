import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ResidentWorkspace } from './ResidentWorkspace';

const mockResidentState = {
  workspace: 'X:/Git/Harborpilot',
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
  ],
  decisions: [
    {
      decision_id: 'decision-1',
      actor: 'pm',
      stage: 'planning',
      summary: 'Selected bounded decomposition strategy',
      timestamp: '2026-03-07T00:00:00Z',
      verdict: 'success',
      strategy_tags: ['task_split'],
      confidence: 0.92,
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
    name: 'Software Engineering AGI',
    mission: 'Ship governed software engineering improvements',
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
  refresh: vi.fn(),
  isActing: vi.fn(() => false),
  start: vi.fn(),
  stop: vi.fn(),
  tick: vi.fn(),
  saveIdentity: vi.fn(),
  createGoal: vi.fn(async () => ({ goal_id: 'goal-new' })),
  approveGoal: vi.fn(async () => null),
  rejectGoal: vi.fn(async () => null),
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
        workspace="X:/Git/Harborpilot"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
      />,
    );

    expect(screen.getByText('Software Engineering AGI')).toBeInTheDocument();
    expect(screen.getByText('Latest Meta-Cognition')).toBeInTheDocument();
    expect(screen.getByText('Task decomposition')).toBeInTheDocument();
  });

  it('creates a goal from the AGI console', async () => {
    render(
      <ResidentWorkspace
        workspace="X:/Git/Harborpilot"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    expect(screen.getByText('Goal Synthesis Console')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Tighten Director retries' } });
    fireEvent.change(screen.getByLabelText('Motivation'), {
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
        workspace="X:/Git/Harborpilot"
        onBackToMain={vi.fn()}
        residentSnapshot={null}
        initialTab="goals"
      />,
    );

    expect(screen.getByRole('button', { name: '暂存' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂存' }));
    fireEvent.click(screen.getByRole('button', { name: '写入 PM' }));
    fireEvent.click(screen.getByRole('button', { name: '交给 PM' }));

    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(1, 'goal-approved', false);
    expect(mockResidentState.stageGoal).toHaveBeenNthCalledWith(2, 'goal-approved', true);
    expect(mockResidentState.runGoal).toHaveBeenCalledWith('goal-approved', false, 1);
  });
});
