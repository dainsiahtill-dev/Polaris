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

  it('puts readable task titles before sequence and priority metadata in the task queue', () => {
    render(
      <ProjectProgressPanel
        tasks={[
          {
            id: 1 as unknown as string,
            title: 1 as unknown as string,
            subject: '实现创建语义化 HTML5 简历结构',
            goal: '构建个人简历的语义化 HTML5 骨架',
            priority: 1,
            status: 'in_progress',
          },
          {
            id: 2 as unknown as string,
            title: 2 as unknown as string,
            subject: '实现响应式 CSS3 样式表',
            goal: '通过 CSS Grid 和 Flexbox 适配桌面与移动端',
            priority: 1,
            status: 'pending',
          },
          {
            id: 3 as unknown as string,
            title: 3 as unknown as string,
            subject: '交付验证与 README 编写',
            goal: '完成 QA 闭环并编写运行说明',
            priority: 1,
            status: 'pending',
          },
        ]}
        pmRunning={false}
      />,
    );

    const rows = screen.getAllByTestId('project-task-item');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent(/^实现创建语义化 HTML5 简历结构/);
    expect(rows[1]).toHaveTextContent(/^实现响应式 CSS3 样式表/);
    expect(rows[2]).toHaveTextContent(/^交付验证与 README 编写/);
  });
});
