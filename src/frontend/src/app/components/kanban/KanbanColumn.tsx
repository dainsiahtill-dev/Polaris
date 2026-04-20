import { memo } from 'react';
import { Droppable } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { MoreHorizontal, Plus } from 'lucide-react';
import { KanbanCard } from './KanbanCard';
import { COLUMN_CONFIG, type KanbanColumn as KanbanColumnType, type KanbanTask } from './types';

interface KanbanColumnProps {
  column: KanbanColumnType;
  completedIds: Set<string>;
  currentTaskId?: string;
  onTaskClick?: (task: KanbanTask) => void;
  onAddTask?: (status: KanbanColumnType['id']) => void;
}

function KanbanColumnComponent({
  column,
  completedIds,
  currentTaskId,
  onTaskClick,
  onAddTask,
}: KanbanColumnProps) {
  const config = COLUMN_CONFIG[column.id];

  return (
    <div className="kanban-column flex-shrink-0 w-[280px]">
      {/* Column Header */}
      <div className={`flex items-center justify-between mb-3 px-1`}>
        <div className="flex items-center gap-2">
          <div className={`size-2 rounded-full ${column.id === 'backlog' ? 'bg-slate-500' : column.id === 'todo' ? 'bg-blue-500' : column.id === 'in_progress' ? 'bg-amber-500' : 'bg-emerald-500'}`} />
          <h3 className="font-semibold text-sm text-text-main">{column.title}</h3>
          <span className="text-xs text-text-dim bg-white/5 px-1.5 py-0.5 rounded-full">
            {column.tasks.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {onAddTask && (
            <button
              type="button"
              onClick={() => onAddTask(column.id)}
              className="p-1 rounded hover:bg-white/10 text-text-dim hover:text-text-main transition-colors"
              title="Add task"
            >
              <Plus className="size-3.5" />
            </button>
          )}
          <button
            type="button"
            className="p-1 rounded hover:bg-white/10 text-text-dim hover:text-text-main transition-colors"
            title="Column options"
          >
            <MoreHorizontal className="size-3.5" />
          </button>
        </div>
      </div>

      {/* Droppable Area */}
      <Droppable droppableId={column.id}>
        {(provided, snapshot) => (
          <motion.div
            ref={provided.innerRef}
            {...provided.droppableProps}
            className={`kanban-column-content min-h-[200px] rounded-lg p-2 transition-colors ${
              snapshot.isDraggingOver
                ? 'bg-accent/10 border-2 border-dashed border-accent/30'
                : 'bg-white/[0.02] border border-transparent'
            }`}
            layout
          >
            {column.tasks.length === 0 && !snapshot.isDraggingOver ? (
              <div className="h-full flex items-center justify-center text-xs text-text-dim opacity-50 py-8">
                No tasks
              </div>
            ) : (
              column.tasks.map((task, index) => (
                <KanbanCard
                  key={task.id}
                  task={task}
                  index={index}
                  isCompleted={completedIds.has(task.id) || task.completed || task.done}
                  isCurrent={currentTaskId === task.id}
                />
              ))
            )}
            {provided.placeholder}
          </motion.div>
        )}
      </Droppable>
    </div>
  );
}

export const KanbanColumn = memo(KanbanColumnComponent);
