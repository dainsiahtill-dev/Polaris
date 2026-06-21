import { Skeleton } from '@/app/components/ui/skeleton';

interface FileViewerSkeletonProps {
  lines?: number;
}

export function FileViewerSkeleton({ lines = 8 }: FileViewerSkeletonProps) {
  return (
    <div className="soft-panel-subtle h-full flex flex-col">
      {/* Header skeleton */}
      <div className="soft-panel-subtle px-4 py-3 border-b">
        <div className="flex items-center justify-between">
          <div className="space-y-2">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-48" />
          </div>
          <div className="flex items-center gap-2">
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-5 w-12" />
          </div>
        </div>
      </div>

      {/* Content skeleton */}
      <div className="flex-1 p-4">
        <div className="space-y-2">
          {Array.from({ length: lines }).map((_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      </div>
    </div>
  );
}
