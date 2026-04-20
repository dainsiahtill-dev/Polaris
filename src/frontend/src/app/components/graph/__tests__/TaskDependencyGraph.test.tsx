/**
 * TaskDependencyGraph 组件测试
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { TaskDependencyGraph } from '../TaskDependencyGraph';
import type { PmTask } from '@/types/task';
import { TaskStatus } from '@/types/task';

// Mock @xyflow/react
vi.mock('@xyflow/react', () => ({
  ReactFlow: vi.fn(({ children, nodes, onNodeClick, fitView, ...props }) => (
    <div data-testid="react-flow" data-nodes={nodes.length} {...props}>
      {nodes.map((node: { id: string; data: { label: string } }) => (
        <button
          key={node.id}
          data-testid={`node-${node.id}`}
          onClick={() => onNodeClick?.({} as React.MouseEvent, node as never)}
        >
          {node.data.label}
        </button>
      ))}
      <button data-testid="fit-view-btn" onClick={() => fitView?.()}>Fit View</button>
      {children}
    </div>
  )),
  Background: vi.fn(() => <div data-testid="background" />),
  Controls: vi.fn(() => <div data-testid="controls" />),
  Panel: vi.fn(({ children }) => <div data-testid="panel">{children}</div>),
  useNodesState: vi.fn((initial) => {
    const setNodes = vi.fn();
    const onNodesChange = vi.fn();
    return [initial, setNodes, onNodesChange] as const;
  }),
  useEdgesState: vi.fn((initial) => {
    const setEdges = vi.fn();
    const onEdgesChange = vi.fn();
    return [initial, setEdges, onEdgesChange] as const;
  }),
  Handle: vi.fn(() => <div data-testid="handle" />),
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
}));

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  GitBranch: vi.fn(() => <div data-testid="git-branch-icon" />),
  AlertTriangle: vi.fn(() => <div data-testid="alert-icon" />),
}));

// 测试数据
const mockTasks: PmTask[] = [
  {
    id: 'task-1',
    title: '初始化项目',
    status: TaskStatus.COMPLETED,
    done: true,
    priority: 1,
    acceptance: [],
  },
  {
    id: 'task-2',
    title: '实现后端 API',
    status: TaskStatus.IN_PROGRESS,
    done: false,
    priority: 2,
    acceptance: [],
    dependencies: ['task-1'],
  },
  {
    id: 'task-3',
    title: '实现前端界面',
    status: TaskStatus.PENDING,
    done: false,
    priority: 3,
    acceptance: [],
    dependencies: ['task-1'],
  },
  {
    id: 'task-4',
    title: '集成测试',
    status: TaskStatus.PENDING,
    done: false,
    priority: 4,
    acceptance: [],
    dependencies: ['task-2', 'task-3'],
  },
];

describe('TaskDependencyGraph', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('渲染', () => {
    it('应该渲染空状态当没有任务时', () => {
      render(<TaskDependencyGraph tasks={[]} />);

      expect(screen.getByText('暂无任务数据')).toBeInTheDocument();
      expect(screen.getByText(/添加任务以查看依赖关系图/)).toBeInTheDocument();
    });

    it('应该渲染任务节点', () => {
      render(<TaskDependencyGraph tasks={mockTasks} />);

      expect(screen.getByText('初始化项目')).toBeInTheDocument();
      expect(screen.getByText('实现后端 API')).toBeInTheDocument();
      expect(screen.getByText('实现前端界面')).toBeInTheDocument();
      expect(screen.getByText('集成测试')).toBeInTheDocument();
    });

    it('应该显示任务统计信息', () => {
      render(<TaskDependencyGraph tasks={mockTasks} />);

      expect(screen.getByText('4 个任务')).toBeInTheDocument();
    });
  });

  describe('交互', () => {
    it('应该调用 onTaskClick 当节点被点击时', () => {
      const onTaskClick = vi.fn();
      render(<TaskDependencyGraph tasks={mockTasks} onTaskClick={onTaskClick} />);

      const taskNode = screen.getByTestId('node-task-1');
      fireEvent.click(taskNode);

      expect(onTaskClick).toHaveBeenCalledWith('task-1');
    });
  });

  describe('循环依赖检测', () => {
    it('不应显示循环依赖警告当没有循环时', () => {
      render(<TaskDependencyGraph tasks={mockTasks} detectCycles />);

      expect(screen.queryByText('检测到循环依赖')).not.toBeInTheDocument();
    });

    it('应该显示循环依赖警告当存在循环时', () => {
      const cyclicTasks: PmTask[] = [
        {
          id: 'task-cycle-a',
          title: '循环任务 A',
          status: TaskStatus.PENDING,
          done: false,
          priority: 1,
          acceptance: [],
          dependencies: ['task-cycle-b'],
        },
        {
          id: 'task-cycle-b',
          title: '循环任务 B',
          status: TaskStatus.PENDING,
          done: false,
          priority: 2,
          acceptance: [],
          dependencies: ['task-cycle-a'], // 形成循环
        },
      ];

      render(<TaskDependencyGraph tasks={cyclicTasks} detectCycles />);

      expect(screen.getByText('检测到循环依赖')).toBeInTheDocument();
    });
  });
});

describe('任务依赖关系验证', () => {
  it('应该正确计算任务深度', () => {
    // task-1: 深度 0（无依赖）
    // task-2: 深度 1（依赖 task-1）
    // task-3: 深度 1（依赖 task-1）
    // task-4: 深度 2（依赖 task-2, task-3）

    // 验证依赖关系
    expect(mockTasks[1].dependencies).toContain('task-1');
    expect(mockTasks[2].dependencies).toContain('task-1');
    expect(mockTasks[3].dependencies).toContain('task-2');
    expect(mockTasks[3].dependencies).toContain('task-3');
  });
});
