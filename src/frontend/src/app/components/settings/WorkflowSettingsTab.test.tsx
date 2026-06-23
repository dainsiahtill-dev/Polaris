import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkflowSettingsTab } from './WorkflowSettingsTab';

const baseSettings = {
  director_iterations: 1,
  director_execution_mode: 'parallel',
  director_max_parallel_tasks: 3,
  director_ready_timeout_seconds: 30,
  director_claim_timeout_seconds: 30,
  director_phase_timeout_seconds: 900,
  director_complete_timeout_seconds: 30,
  director_task_timeout_seconds: 3600,
  director_forever: false,
  director_show_output: true,
  pm_runs_director: true,
  pm_director_show_output: true,
  pm_director_timeout: 600,
  pm_director_iterations: 1,
  pm_director_match_mode: 'latest',
  pm_max_failures: 5,
  pm_max_blocked: 5,
  pm_max_same: 3,
  qa_enabled: true,
  slm_enabled: false,
  verifier_policy: {
    browser_enabled: false,
    visual_enabled: false,
    multimodal_llm_enabled: false,
    user_scripts_enabled: false,
    domain_verifiers_enabled: false,
    required_evidence_modalities: [],
  },
};

describe('WorkflowSettingsTab', () => {
  it('shows platform verifier policy as optional capabilities', () => {
    render(<WorkflowSettingsTab settings={baseSettings} onSave={vi.fn()} />);

    expect(screen.getByText('验收能力策略')).toBeInTheDocument();
    expect(screen.getByText('可选证据模态')).toBeInTheDocument();
    expect(screen.getByText(/不会隐式要求用户安装浏览器或视觉环境/)).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Browser 验收' })).toHaveAttribute('data-state', 'unchecked');
  });

  it('persists verifier policy under platform settings payload', async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<WorkflowSettingsTab settings={baseSettings} onSave={onSave} />);

    fireEvent.click(screen.getByRole('switch', { name: 'Browser 验收' }));
    fireEvent.click(screen.getByRole('switch', { name: 'Visual 验收' }));
    fireEvent.click(screen.getByRole('switch', { name: '用户脚本验证' }));
    fireEvent.click(screen.getByRole('button', { name: /保存设置/ }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        verifier_policy: {
          browser_enabled: true,
          visual_enabled: true,
          multimodal_llm_enabled: false,
          user_scripts_enabled: true,
          domain_verifiers_enabled: false,
          required_evidence_modalities: [],
        },
      })
    );
  });
});
