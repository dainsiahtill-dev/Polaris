export type ProgressMode = 'done' | 'position' | 'idle' | 'success';

export type { PmTask, TaskStatus, AcceptanceCriteria } from '@/types/task';

export interface TaskQueueItem {
  key: string;
  title: string;
  id?: string;
  isCurrent?: boolean;
  isCompleted?: boolean;
}
