interface CyberpunkTestAnimationProps {
  progress?: number;
  status: 'running' | 'success' | 'failed';
}

export function CyberpunkTestAnimation({ progress = 0, status }: CyberpunkTestAnimationProps) {
  if (status !== 'running') {
    return null;
  }

  return (
    <div className="relative inline-flex items-center gap-2">
      <div className="relative flex items-center gap-1 px-3 py-1">
        <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_0_3px_rgba(47,127,120,0.12)]" />
        <span className="h-1.5 w-1.5 rounded-full bg-accent/70" />
        <span className="h-1.5 w-1.5 rounded-full bg-accent/40" />
      </div>
      <div className="relative flex h-6 items-center overflow-hidden">
        <span className="relative font-mono text-sm text-accent-text">
          扫描中
        </span>
      </div>
      {progress > 0 && (
        <div className="soft-inset relative h-1.5 w-24 overflow-hidden rounded-full">
          <div className="soft-progress absolute inset-0 transition-all duration-300" style={{
            width: `${progress}%`
          }} />
        </div>
      )}
    </div>
  );
}

interface CyberpunkStatusBorderProps {
  children: React.ReactNode;
  status: 'running' | 'success' | 'failed';
  className?: string;
}

export function CyberpunkStatusBorder({ children, status, className = '' }: CyberpunkStatusBorderProps) {
  const statusClass = {
    running: 'border-accent/45 shadow-[0_14px_34px_rgba(47,127,120,0.16)]',
    success: 'border-status-success/45 shadow-[0_14px_34px_rgba(40,122,85,0.14)]',
    failed: 'border-status-error/45 shadow-[0_14px_34px_rgba(182,63,73,0.14)]',
  }[status];

  if (status === 'running') {
    return (
      <div className={`relative ${className}`}>
        <div className={`soft-raised relative rounded-lg ${statusClass}`}>
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className={`relative ${className}`}>
      <div className={`soft-panel-subtle relative rounded-lg ${statusClass}`}>
        {children}
      </div>
    </div>
  );
}

interface CyberpunkCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
  status: 'running' | 'success' | 'failed' | 'unknown';
  className?: string;
}

export function CyberpunkCard({ children, status, className = '', ...rest }: CyberpunkCardProps) {
  const statusColors = {
    running: {
      border: 'border-accent/45',
      bg: 'bg-accent/10',
      shadow: 'shadow-[0_14px_34px_rgba(47,127,120,0.14)]',
    },
    success: {
      border: 'border-status-success/45',
      bg: 'bg-status-success/10',
      shadow: 'shadow-[0_14px_34px_rgba(40,122,85,0.12)]',
    },
    failed: {
      border: 'border-status-error/45',
      bg: 'bg-status-error/10',
      shadow: 'shadow-[0_14px_34px_rgba(182,63,73,0.12)]',
    },
    unknown: {
      border: 'border-status-warning/45',
      bg: 'bg-status-warning/10',
      shadow: 'shadow-[0_14px_34px_rgba(199,130,24,0.12)]',
    },
  };

  const colors = statusColors[status];

  return (
    <div className={`soft-panel-subtle rounded-lg border ${colors.border} ${colors.bg} ${colors.shadow} ${className}`} {...rest}>
      {children}
    </div>
  );
}

interface CyberpunkGlitchTextProps {
  text: string;
  status: 'unknown' | 'running' | 'success' | 'failed';
  className?: string;
}

export function CyberpunkGlitchText({ text, status, className = '' }: CyberpunkGlitchTextProps) {
  if (status === 'success') {
    return (
      <span className={`font-mono text-status-success ${className}`}>
        {text}
      </span>
    );
  }

  if (status === 'failed') {
    return (
      <span className={`font-mono text-status-error ${className}`}>
        {text}
      </span>
    );
  }

  if (status === 'running') {
    return (
      <span className={`font-mono text-accent-text ${className}`}>
        {text}
      </span>
    );
  }

  return (
    <span className={`font-mono text-status-warning ${className}`}>
      {text}
    </span>
  );
}
