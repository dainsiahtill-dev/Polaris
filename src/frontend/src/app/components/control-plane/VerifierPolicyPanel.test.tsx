import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VerifierPolicyPanel } from './VerifierPolicyPanel';
import { getVerifierPolicy, updateVerifierPolicy } from '@/services/controlPlane';
import type { VerifierPolicy } from '@/services/controlPlane';

vi.mock('@/services/controlPlane', () => ({
  getVerifierPolicy: vi.fn(),
  updateVerifierPolicy: vi.fn(),
}));

const basePolicy: VerifierPolicy = {
  schema_version: 1,
  source: 'control_plane.verifier_policy',
  workspace: '/tmp/workspace',
  config_path: '/tmp/workspace/.polaris/verifier_policy.json',
  enabled_modalities: [],
  required_modalities: [],
  custom_scripts: [],
  capabilities: {
    browser: {
      enabled: false,
      required: false,
      available: false,
      reason: 'Set KERNELONE_BROWSER_VERIFIER_AVAILABLE=1.',
    },
    visual: {
      enabled: false,
      required: false,
      available: false,
      reason: 'Requires browser verifier support and KERNELONE_MULTIMODAL_QA_ENABLED=1.',
    },
    llm_judge: {
      enabled: false,
      required: false,
      available: false,
      reason: 'Set KERNELONE_MULTIMODAL_QA_ENABLED=1.',
    },
    custom_script: {
      enabled: false,
      required: false,
      available: false,
      reason: 'Set KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED=1.',
    },
  },
  environment: {
    browser: { available: false, reason: 'Set KERNELONE_BROWSER_VERIFIER_AVAILABLE=1.' },
    visual: { available: false, reason: 'Requires browser verifier support and KERNELONE_MULTIMODAL_QA_ENABLED=1.' },
    llm_judge: { available: false, reason: 'Set KERNELONE_MULTIMODAL_QA_ENABLED=1.' },
    custom_script: { available: false, reason: 'Set KERNELONE_CUSTOM_VERIFIER_SCRIPTS_ENABLED=1.' },
  },
  safety: {
    optional_by_default: true,
    internal_harness_owned: false,
    executes_verifiers: false,
    requires_explicit_user_enablement: true,
  },
};

const supportedPolicy: VerifierPolicy = {
  ...basePolicy,
  capabilities: {
    ...basePolicy.capabilities,
    browser: {
      enabled: false,
      required: false,
      available: true,
      reason: 'Playwright browser verifier is available.',
    },
    custom_script: {
      enabled: false,
      required: false,
      available: true,
      reason: 'Custom verifier scripts are allowed.',
    },
  },
  environment: {
    ...basePolicy.environment,
    browser: { available: true, reason: 'Playwright browser verifier is available.' },
    custom_script: { available: true, reason: 'Custom verifier scripts are allowed.' },
  },
};

describe('VerifierPolicyPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getVerifierPolicy).mockResolvedValue({ ok: true, data: basePolicy });
    vi.mocked(updateVerifierPolicy).mockResolvedValue({ ok: true, data: basePolicy });
  });

  it('keeps unavailable verifier modalities optional unless the environment supports them', async () => {
    render(<VerifierPolicyPanel />);

    await screen.findByText('Browser 验收');
    const browserEnable = screen.getAllByLabelText('启用', {
      selector: 'input[type="checkbox"]',
    })[0];
    const requiredInputs = screen.getAllByLabelText('设为必需证据', {
      selector: 'input[type="checkbox"]',
    });

    fireEvent.click(browserEnable);

    expect(requiredInputs[0]).toBeDisabled();
    expect(
      screen.getByText('当前环境未声明该能力；可以保留启用意图，但不能新增为必需证据。'),
    ).toBeInTheDocument();
  });

  it('persists optional verifier switches and user scripts through the platform policy API', async () => {
    vi.mocked(getVerifierPolicy).mockResolvedValueOnce({ ok: true, data: supportedPolicy });

    render(<VerifierPolicyPanel />);

    await screen.findByText('Browser 验收');

    const enabledInputs = screen.getAllByLabelText('启用', {
      selector: 'input[type="checkbox"]',
    });
    const requiredInputs = screen.getAllByLabelText('设为必需证据', {
      selector: 'input[type="checkbox"]',
    });

    fireEvent.click(enabledInputs[0]);
    fireEvent.click(requiredInputs[0]);
    fireEvent.change(screen.getByPlaceholderText('tests/physics_verifier.py'), {
      target: { value: './tests/physics_verifier.py' },
    });
    fireEvent.click(screen.getByText('添加'));
    fireEvent.click(screen.getByText('保存策略'));

    await waitFor(() => {
      expect(updateVerifierPolicy).toHaveBeenCalledWith({
        browser_enabled: true,
        visual_enabled: false,
        llm_judge_enabled: false,
        custom_script_enabled: true,
        required_modalities: ['browser'],
        custom_scripts: [
          {
            id: 'physics_verifier',
            path: 'tests/physics_verifier.py',
            modality: 'custom_script',
            enabled: true,
            required: false,
          },
        ],
      });
    });
  });
});
