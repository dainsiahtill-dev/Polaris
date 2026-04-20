import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { KanbanBoard, convertToKanbanTask } from '../KanbanBoard';
import type { KanbanTask } from '../types';

// Mock framer-motion
vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children }: { children: React.ReactNode }) => children,
  },
}));

// Mock @hello-pangea/dnd with correct function signatures
vi.mock('@hello-pangea/dnd', () => ({
  DragDropContext: ({ children }: { children: React.ReactNode }) => children,
  Droppable: ({
    children,
    droppableId,
  }: {
    children: (props: { innerRef: (el: HTMLElement | null) => void; droppableProps: object; placeholder: React.ReactNode }, snapshot: { isDraggingOver: boolean }) => React.ReactNode;
    droppableId: string;
  }) => {
    const mockProvided = {
      innerRef: vi.fn(),
      droppableProps: {},
      placeholder: null,
    };
    const mockSnapshot = { isDraggingOver: false };
    return children(mockProvided as never, mockSnapshot as never);
  },
  Draggable: ({
    children,
    draggableId,
    index,
  }: {
    children: (props: { innerRef: (el: HTMLElement | null) => void; draggableProps: object; dragHandleProps: object }, snapshot: { isDragging: boolean }) => React.ReactNode;
    draggableId: string;
    index: number;
  }) => {
    const mockProvided = {
      innerRef: vi.fn(),
      draggableProps: {},
      dragHandleProps: {},
    };
    const mockSnapshot = { isDragging: false };
    return children(mockProvided as never, mockSnapshot as never);
  },
}));

describe('KanbanBoard', () => {
  const mockTasks: KanbanTask[] = [
    {
      id: 'task-1',
      title: 'Task 1',
      goal: 'Goal 1',
      priority: 'high',
      status: 'todo',
      done: false,
    },
    {
      id: 'task-2',
      title: 'Task 2',
      priority: 'medium',
      status: 'in_progress',
      done: false,
    },
    {
      id: 'task-3',
      title: 'Task 3',
      priority: 'low',
      status: 'done',
      done: true,
    },
  ];

  const mockOnTaskMove = vi.fn();
  const completedIds = new Set<string>();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders all columns', () => {
    render(
      <KanbanBoard
        tasks={mockTasks}
        completedIds={completedIds}
        onTaskMove={mockOnTaskMove}
      />
    );

    expect(screen.getByText('Backlog')).toBeInTheDocument();
    expect(screen.getByText('To Do')).toBeInTheDocument();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
    expect(screen.getByText('Done')).toBeInTheDocument();
  });

  it('renders tasks in correct columns', () => {
    render(
      <KanbanBoard
        tasks={mockTasks}
        completedIds={completedIds}
        onTaskMove={mockOnTaskMove}
      />
    );

    expect(screen.getByText('Task 1')).toBeInTheDocument();
    expect(screen.getByText('Task 2')).toBeInTheDocument();
    expect(screen.getByText('Task 3')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(
      <KanbanBoard
        tasks={[]}
        completedIds={completedIds}
        onTaskMove={mockOnTaskMove}
        isLoading={true}
      />
    );

    expect(screen.getByText('Loading tasks...')).toBeInTheDocument();
  });

  it('displays task count per column', () => {
    render(
      <KanbanBoard
        tasks={mockTasks}
        completedIds={completedIds}
        onTaskMove={mockOnTaskMove}
      />
    );

    // Check column counts are displayed
    const todoColumn = screen.getByText('To Do').closest('.kanban-column');
    expect(todoColumn?.textContent).toContain('1');
  });
});

describe('convertToKanbanTask', () => {
  it('converts basic PmTask to KanbanTask', () => {
    const pmTask = {
      id: 'pm-1',
      title: 'Test Task',
      goal: 'Test Goal',
      priority: 3,
    };

    const kanbanTask = convertToKanbanTask(pmTask);

    expect(kanbanTask.id).toBe('pm-1');
    expect(kanbanTask.title).toBe('Test Task');
    expect(kanbanTask.goal).toBe('Test Goal');
    expect(kanbanTask.priority).toBe('high');
    expect(kanbanTask.status).toBe('todo');
  });

  it('maps priority numbers correctly', () => {
    expect(convertToKanbanTask({ id: '1', priority: 5 }).priority).toBe('urgent');
    expect(convertToKanbanTask({ id: '2', priority: 4 }).priority).toBe('urgent');
    expect(convertToKanbanTask({ id: '3', priority: 3 }).priority).toBe('high');
    expect(convertToKanbanTask({ id: '4', priority: 2 }).priority).toBe('medium');
    expect(convertToKanbanTask({ id: '5', priority: 1 }).priority).toBe('low');
    expect(convertToKanbanTask({ id: '6', priority: 0 }).priority).toBe('low');
  });

  it('maps status strings correctly', () => {
    expect(convertToKanbanTask({ id: '1', status: 'backlog' }).status).toBe('backlog');
    expect(convertToKanbanTask({ id: '2', status: 'todo' }).status).toBe('todo');
    expect(convertToKanbanTask({ id: '3', status: 'pending' }).status).toBe('todo');
    expect(convertToKanbanTask({ id: '4', status: 'in_progress' }).status).toBe('in_progress');
    expect(convertToKanbanTask({ id: '5', status: 'running' }).status).toBe('in_progress');
    expect(convertToKanbanTask({ id: '6', status: 'done' }).status).toBe('done');
    expect(convertToKanbanTask({ id: '7', status: 'completed' }).status).toBe('done');
    expect(convertToKanbanTask({ id: '8', status: 'success' }).status).toBe('done');
  });

  it('handles empty title fallback', () => {
    const result = convertToKanbanTask({ id: '1' });
    expect(result.title).toBe('Untitled Task');
  });
});
