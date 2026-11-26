import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { makeEngineeringBinding } from '@/test/engineeringRunFixtures';
import { GoalComposer } from './GoalComposer';
import { parseResearchPlan } from '@/runtime/researchPlan';

const plan = {
  schema: 'engineering.research-plan.v1',
  project: '相位',
  experiments: [
    { lab: 'geometry', id: 'sample', scope: '有限输入' },
    { lab: 'optics', id: 'sample', scope: '独立输入' },
  ],
};

function composer() {
  const binding = makeEngineeringBinding('/bound');
  render(<GoalComposer binding={binding} workspace="/bound" role="director" />);
  fireEvent.change(screen.getByLabelText('工程目标'), { target: { value: '审查边界' } });
  return binding;
}

function selectFile(file: File) {
  fireEvent.change(screen.getByLabelText(/研究计划文件/), { target: { files: [file] } });
}

describe('research plan enrollment', () => {
  it('accepts a declared dependency pending its first publication and rejects an invalid pin', () => {
    const edge = { lab: 'geometry', id: 'sample' };
    const draft = { ...plan, experiments: [plan.experiments[0], { ...plan.experiments[1], depends_on: [edge] }] };
    expect(parseResearchPlan(draft)).toEqual(draft);
    expect(() => parseResearchPlan({
      ...draft, experiments: [plan.experiments[0], { ...plan.experiments[1], depends_on: [{ ...edge, sha256: 'bad' }] }],
    })).toThrow(/SHA-256/);
  });

  it('requires a chosen laboratory, keeps role workflow independent and submits the complete plan', async () => {
    const binding = composer();
    fireEvent.click(screen.getByRole('radio', { name: /协作执行/ }));
    fireEvent.click(screen.getByLabelText('多实验室研究'));
    const start = screen.getByRole('button', { name: '开始执行' });
    expect(start).toBeDisabled();
    selectFile(new File([JSON.stringify(plan)], 'plan.json', { type: 'application/json' }));
    const lab = await screen.findByLabelText('本次实验室');
    expect(start).toBeDisabled();
    fireEvent.change(lab, { target: { value: 'optics' } });
    fireEvent.click(start);
    await waitFor(() => expect(binding.start).toHaveBeenCalledWith({
      instruction: '审查边界', role: 'director', workflow: 'collaborative', required_checks: [], research: { lab: 'optics', plan },
    }));
  });

  it('invalidates a prior selection on a malformed UTF-8 import and restores ordinary runs only on opt-out', async () => {
    const binding = composer();
    const mode = screen.getByLabelText('多实验室研究');
    fireEvent.click(mode);
    selectFile(new File([JSON.stringify({ ...plan, experiments: [plan.experiments[0]] })], 'good.json'));
    await screen.findByLabelText('本次实验室');
    selectFile(new File([new Uint8Array([0xff, 0xfe])], 'bad.json'));
    expect(await screen.findByRole('alert')).toHaveTextContent(/UTF-8/);
    const start = screen.getByRole('button', { name: '开始执行' });
    expect(start).toBeDisabled();
    fireEvent.click(start);
    expect(binding.start).not.toHaveBeenCalled();
    fireEvent.click(mode);
    fireEvent.click(start);
    expect(binding.start).toHaveBeenCalledWith({ instruction: '审查边界', role: 'director', workflow: 'direct', required_checks: [] });
  });

  it('rejects ambiguous JSON and duplicate experiment identities', async () => {
    composer();
    fireEvent.click(screen.getByLabelText('多实验室研究'));
    selectFile(new File([JSON.stringify(plan).replace('"project":"相位"', '"project":"旧","pro\\u006aect":"相位"')], 'duplicate-key.json'));
    expect(await screen.findByRole('alert')).toHaveTextContent('重复 JSON 键');
    selectFile(new File([JSON.stringify({ ...plan, experiments: [plan.experiments[0], plan.experiments[0]] })], 'duplicate-experiment.json'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('实验身份重复'));
    expect(screen.getByRole('button', { name: '开始执行' })).toBeDisabled();
  });
});
