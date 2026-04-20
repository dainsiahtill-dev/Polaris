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
      <div className="relative flex items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 opacity-20 blur-lg animate-pulse" />
        <div className="absolute inset-0 rounded-full bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 animate-[spin_2s_linear_infinite]" style={{
          background: 'conic-gradient(from 0deg, #22d3ee, #a855f7, #ec4899, #22d3ee)',
          padding: '2px'
        }}>
          <div className="w-full h-full rounded-full bg-gray-950" />
        </div>
        <div className="relative flex items-center gap-1 px-3 py-1">
          <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-[bounce_0.5s_ease-in-out_infinite]" style={{ animationDelay: '0ms' }} />
          <span className="w-1.5 h-1.5 rounded-full bg-purple-400 animate-[bounce_0.5s_ease-in-out_infinite]" style={{ animationDelay: '150ms' }} />
          <span className="w-1.5 h-1.5 rounded-full bg-pink-400 animate-[bounce_0.5s_ease-in-out_infinite]" style={{ animationDelay: '300ms' }} />
        </div>
      </div>
      <div className="relative overflow-hidden h-6 flex items-center">
        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-cyan-400/20 to-transparent animate-[shimmer_1.5s_ease-in-out_infinite]" />
        <span className="relative font-mono text-sm bg-gradient-to-r from-cyan-400 via-purple-400 to-pink-400 bg-clip-text text-transparent animate-[textGlow_2s_ease-in-out_infinite]">
          扫描中
        </span>
      </div>
      {progress > 0 && (
        <div className="relative w-24 h-1.5 rounded-full overflow-hidden bg-gray-800/50">
          <div className="absolute inset-0 bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 animate-[loading_1s_ease-in-out_infinite]" style={{
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
  if (status === 'running') {
    return (
      <div className={`relative ${className}`}>
        <div className="absolute -inset-0.5 rounded-xl bg-gradient-to-r from-cyan-400 via-purple-500 to-pink-500 opacity-75 blur-sm animate-[pulse_1.5s_ease-in-out_infinite]" />
        <div className="absolute inset-0 rounded-xl overflow-hidden">
          <div className="absolute inset-0 bg-[linear-gradient(90deg,transparent,rgba(34,211,238,0.1),transparent)] animate-[scan_2s_ease-in-out_infinite]" style={{
            background: 'linear-gradient(90deg, transparent 0%, rgba(34,211,238,0.15) 50%, transparent 100%)',
            transform: 'translateX(-100%)'
          }} />
        </div>
        <div className="relative rounded-xl bg-gray-900">
          {children}
        </div>
      </div>
    );
  }

  if (status === 'success') {
    return (
      <div className={`relative ${className}`}>
        <div className="absolute -inset-0.5 rounded-xl bg-gradient-to-r from-emerald-400 to-cyan-500 opacity-50 blur-sm" />
        <div className="relative rounded-xl bg-gray-900 border border-emerald-500/30">
          {children}
        </div>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className={`relative ${className}`}>
        <div className="absolute -inset-0.5 rounded-xl bg-gradient-to-r from-rose-500 to-orange-500 opacity-50 blur-sm animate-[shake_0.5s_ease-in-out]" />
        <div className="relative rounded-xl bg-gray-900 border border-rose-500/30">
          {children}
        </div>
      </div>
    );
  }

  return <div className={className}>{children}</div>;
}

interface CyberpunkCardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
  status: 'running' | 'success' | 'failed' | 'unknown';
  className?: string;
}

export function CyberpunkCard({ children, status, className = '', ...rest }: CyberpunkCardProps) {
  const statusColors = {
    running: {
      border: 'border-cyan-500/50',
      bg: 'bg-cyan-500/5',
      glow: 'shadow-[0_0_30px_rgba(34,211,238,0.2)]',
      gradient: 'from-cyan-400 via-purple-500 to-pink-500',
    },
    success: {
      border: 'border-emerald-500/40',
      bg: 'bg-emerald-500/5',
      glow: 'shadow-[0_0_24px_rgba(16,185,129,0.18)]',
      gradient: 'from-emerald-400 to-cyan-500',
    },
    failed: {
      border: 'border-rose-500/40',
      bg: 'bg-rose-500/5',
      glow: 'shadow-[0_0_24px_rgba(244,63,94,0.18)]',
      gradient: 'from-rose-500 to-orange-500',
    },
    unknown: {
      border: 'border-amber-500/30',
      bg: 'bg-amber-500/5',
      glow: 'shadow-[0_0_24px_rgba(251,191,36,0.15)]',
      gradient: 'from-amber-400 to-orange-500',
    },
  };

  const colors = statusColors[status];

  if (status === 'running') {
    return (
      <div
        className={`relative overflow-hidden rounded-xl ${colors.border} ${colors.bg} ${colors.glow} ${className}`}
        {...rest}
      >
        <div className="absolute inset-0 overflow-hidden">
          <div className="absolute inset-0 bg-[linear-gradient(90deg,transparent,rgba(34,211,238,0.08),transparent)] animate-[scan_1.5s_ease-in-out_infinite]" />
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400 to-transparent opacity-50" />
          <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-pink-400 to-transparent opacity-50" />
          <div className="absolute top-0 bottom-0 left-0 w-px bg-gradient-to-b from-transparent via-cyan-400 to-transparent opacity-30" />
          <div className="absolute top-0 bottom-0 right-0 w-px bg-gradient-to-b from-transparent via-purple-400 to-transparent opacity-30" />
        </div>
        <div className="relative">
          {children}
        </div>
        <div className="absolute top-2 right-2">
          <div className="flex gap-1">
            <div className="w-2 h-2 rounded-full bg-cyan-400 animate-[bounce_0.6s_ease-in-out_infinite]" style={{ animationDelay: '0ms' }} />
            <div className="w-2 h-2 rounded-full bg-purple-400 animate-[bounce_0.6s_ease-in-out_infinite]" style={{ animationDelay: '200ms' }} />
            <div className="w-2 h-2 rounded-full bg-pink-400 animate-[bounce_0.6s_ease-in-out_infinite]" style={{ animationDelay: '400ms' }} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`rounded-xl border ${colors.border} ${colors.bg} ${colors.glow} ${className}`} {...rest}>
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
  const glitchClass = status === 'running' 
    ? 'animate-[glitch_0.3s_ease-in-out_infinite]' 
    : '';

  if (status === 'success') {
    return (
      <span className={`font-mono text-emerald-400 ${className}`}>
        {text}
      </span>
    );
  }

  if (status === 'failed') {
    return (
      <span className={`font-mono text-rose-400 ${className}`}>
        {text}
      </span>
    );
  }

  return (
    <span className={`font-mono bg-gradient-to-r from-cyan-400 via-purple-400 to-pink-400 bg-clip-text text-transparent ${glitchClass} ${className}`}>
      {text}
    </span>
  );
}
