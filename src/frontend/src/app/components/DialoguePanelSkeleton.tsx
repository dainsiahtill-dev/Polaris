import { Skeleton } from '@/app/components/ui/skeleton';

interface DialoguePanelSkeletonProps {
  items?: number;
}

export function DialoguePanelSkeleton({ items = 3 }: DialoguePanelSkeletonProps) {
  return (
    <div className="h-full bg-[var(--ink-indigo)] border-l border-gray-800 flex flex-col">
      {/* Header skeleton */}
      <div className="px-4 py-3 border-b border-gray-800 bg-[#252526]">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Skeleton className="w-5 h-5 rounded" />
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="flex items-center gap-1">
            <Skeleton className="w-3 h-3 rounded" />
            <Skeleton className="h-3 w-8" />
          </div>
        </div>
        
        {/* Filter buttons skeleton */}
        <div className="flex flex-wrap gap-1.5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-6 w-12 rounded" />
          ))}
        </div>
      </div>

      {/* Content skeleton */}
      <div className="flex-1 p-4 space-y-3">
        {Array.from({ length: items }).map((_, i) => (
          <div key={i} className="flex gap-3">
            <Skeleton className="flex-shrink-0 w-8 h-8 rounded-full" />
            <div className="flex-1 space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="h-3 w-16" />
                <Skeleton className="h-3 w-12" />
              </div>
              <Skeleton className="h-16 w-full rounded" />
            </div>
          </div>
        ))}
      </div>

      {/* Footer stats skeleton */}
      <div className="border-t border-gray-800 p-3 bg-[#252526]">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Skeleton className="h-3 w-16" />
            <Skeleton className="h-3 w-12" />
          </div>
          <div className="flex items-center gap-1">
            <Skeleton className="w-3 h-3 rounded" />
            <Skeleton className="h-3 w-16" />
          </div>
        </div>
      </div>
    </div>
  );
}