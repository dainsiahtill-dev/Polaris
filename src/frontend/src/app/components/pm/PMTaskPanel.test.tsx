import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PMTaskPanel } from './PMTaskPanel';
import { TaskStatus, type PmTask } from '@/types/task';

function makeTask(overrides: Partial<PmTask> = {}): PmTask {
  return {
    id: 'PM-1',
    title: '落地 PM 合同详情',
    goal: '任务详情必须可审计',
    summary: '补齐执行步骤和验收标准',
    status: TaskStatus.PENDING,
    done: false,
    priority: 1,
    acceptance: [{ description: '展示验收标准' }],
    execution_checklist: ['读取 PM 合同', '同步 Director 队列'],
    target_files: ['src/frontend/src/app/components/pm/PMTaskPanel.tsx'],
    dependencies: ['PM-0'],
    qa_contract: { acceptance_criteria: ['QA 能看到合同字段'] },
    metadata: {
      blueprint_id: 'BP-PM-1',
      runtime_blueprint_path: 'runtime/contracts/bp-pm-1.json',
      source: 'runtime_projection',
    },
    ...overrides,
  };
}

describe('PMTaskPanel', () => {
  it('renders PM task contract details without relying on raw JSON only', () => {
    const task = makeTask();
    render(
      <PMTaskPanel
        tasks={[task]}
        selectedTaskId={task.id}
        onTaskSelect={() => undefined}
        pmRunning={false}
      />,
    );

    expect(screen.queryByText('新建')).not.toBeInTheDocument();
    const provenance = screen.getByTestId('pm-task-detail-provenance');
    expect(within(provenance).getByText('BP-PM-1')).toBeInTheDocument();
    expect(screen.getByText('读取 PM 合同')).toBeInTheDocument();
    expect(screen.getByText('展示验收标准')).toBeInTheDocument();
    expect(screen.getByText('QA 能看到合同字段')).toBeInTheDocument();
    expect(screen.getByText('src/frontend/src/app/components/pm/PMTaskPanel.tsx')).toBeInTheDocument();
    expect(screen.getByText('PM-0')).toBeInTheDocument();
  });
});
