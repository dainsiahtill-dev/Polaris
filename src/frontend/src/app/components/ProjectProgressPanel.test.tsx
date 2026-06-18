import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProjectProgressPanel, extractLatestQaEvidence } from './ProjectProgressPanel';
import type { DialogueEvent } from './DialoguePanel';
import type { LogEntry } from './pm';

const qaLog: LogEntry = {
  id: 'qa-1',
  timestamp: '2026-06-02T00:00:00.000Z',
  level: 'success',
  source: 'QA',
  message: 'Project integration QA passed',
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test',
    passed: true,
  },
};

const qaDialogueEvent: DialogueEvent = {
  eventId: 'qa-dialogue-1',
  speaker: 'QA',
  type: 'review',
  content: 'Project integration QA: PASS; Integration verification passed: npm run test -- --watch=false',
  refs: { phase: 'integration_qa' },
  meta: {
    reason: 'integration_qa_passed',
    evidence_grade: 'real_command_passed',
    summary: 'Integration verification passed: npm run test -- --watch=false',
    passed: true,
  },
};

describe('ProjectProgressPanel QA evidence', () => {
  it('extracts the latest integration QA evidence grade from runtime logs', () => {
    expect(extractLatestQaEvidence([qaLog])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test',
      passed: true,
    });
  });

  it('renders QA evidence grade instead of a vague PASS label', () => {
    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[qaLog]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('real command passed');
    expect(evidence).toHaveTextContent('integration_qa_passed');
  });

  it('recovers QA evidence from dialogue transcript metadata after reload', () => {
    expect(extractLatestQaEvidence([], [qaDialogueEvent])).toEqual({
      grade: 'real_command_passed',
      reason: 'integration_qa_passed',
      summary: 'Integration verification passed: npm run test -- --watch=false',
      passed: true,
    });

    render(
      <ProjectProgressPanel
        tasks={[]}
        pmRunning={false}
        executionLogs={[]}
        dialogueEvents={[qaDialogueEvent]}
        currentPhase="qa"
      />,
    );

    const evidence = screen.getByTestId('qa-evidence-grade');
    expect(evidence).toHaveTextContent('QA evidence');
    expect(evidence).toHaveTextContent('real command passed');
    expect(evidence).toHaveTextContent('integration_qa_passed');
  });

  it('renders the PM to Chief Engineer to Director chain and ignores numeric task titles', () => {
    render(
      <ProjectProgressPanel
        tasks={[
          {
            id: 1 as unknown as string,
            title: 1 as unknown as string,
            subject: '实现账户服务 API',
            goal: '交付可运行的账户服务',
            priority: 1,
            status: 'in_progress',
            acceptance: [{ description: '账户服务测试通过' }],
          },
        ]}
        pmRunning={false}
        engineStatus={{
          roles: {
            PM: { status: 'completed', task_id: '1', task_title: '实现账户服务 API' },
            ChiefEngineer: { status: 'running', task_id: '1', task_title: '蓝图审查' },
            Director: { status: 'waiting', task_id: '1', task_title: '等待实现' },
          },
        }}
      />,
    );

    expect(screen.getByTestId('project-chain-heading')).toHaveTextContent('PM → Chief Engineer → Director');
    expect(screen.getByTestId('project-chain-role-pm')).toHaveTextContent('PM');
    expect(screen.getByTestId('project-chain-role-chief-engineer')).toHaveTextContent('Chief Engineer');
    expect(screen.getByTestId('project-chain-role-director')).toHaveTextContent('Director');
    expect(screen.getByTestId('project-task-title')).toHaveTextContent('实现账户服务 API');
    expect(screen.getByTestId('project-task-title')).not.toHaveTextContent(/^1$/);
    expect(screen.queryByText('任务队列（PM → Director）')).not.toBeInTheDocument();
  });
});
