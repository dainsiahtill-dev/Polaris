import { memo, useRef, type CSSProperties } from 'react';
import { Draggable } from '@hello-pangea/dnd';
import { motion } from 'framer-motion';
import { CheckCircle, Clock, ArrowRight } from 'lucide-react';
import { PriorityBadge } from './PriorityBadge';
import type { KanbanTask } from './types';

interface KanbanCardProps {
  task: KanbanTask;
  index: number;
  isCompleted: boolean;
  isCurrent: boolean;
}

function KanbanCardComponent({ task, index, isCompleted, isCurrent }: KanbanCardProps) {
  return (
    <Draggable draggableId={task.id} index={index}>
      {(provided, snapshot) => {
        const cardStyle: CSSProperties = {
          ...provided.draggableProps.style,
        };

        return (
          <div
            ref={provided.innerRef}
            {...provided.draggableProps}
            {...provided.dragHandleProps}
            className={`kanban-card p-3 mb-2 rounded-md border cursor-grab transition-shadow ${
              snapshot.isDragging
                ? 'shadow-xl border-primary bg-card'
                : 'border-border hover:border-primary/50'
            } ${isCurrent ? 'ring-2 ring-accent/50' : ''}`}
          >
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, delay: index * 0.05 }}
              style={cardStyle}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-text-main truncate" title={task.title}>
                    {task.title}
                  </p>
                  {task.goal && (
                    <p className="mt-1 text-xs text-text-muted line-clamp-2" title={task.goal}>
                      {task.goal}
                    </p>
                  )}
                </div>
                <div className="flex-shrink-0">
                  <PriorityBadge priority={task.priority} showLabel={false} />
                </div>
              </div>

              <div className="mt-3 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs text-text-dim">
                  {isCompleted ? (
                    <CheckCircle className="size-3.5 text-status-success" />
                  ) : isCurrent ? (
                    <ArrowRight className="size-3.5 text-accent animate-pulse" />
                  ) : (
                    <Clock className="size-3.5" />
                  )}
                  <span>{isCompleted ? 'Completed' : isCurrent ? 'In Progress' : 'Pending'}</span>
                </div>
                {task.id && (
                  <span className="text-[10px] font-mono text-text-dim opacity-60">
                    #{task.id.slice(-6)}
                  </span>
                )}
              </div>
            </motion.div>
          </div>
        );
      }}
    </Draggable>
  );
}

export const KanbanCard = memo(KanbanCardComponent);
