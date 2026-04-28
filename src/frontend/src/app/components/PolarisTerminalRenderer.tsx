interface PolarisTerminalRendererProps {
  text: string;
  className?: string;
}

export function PolarisTerminalRenderer({ text, className }: PolarisTerminalRendererProps) {
  return (
    <div className={className}>
      <pre className="whitespace-pre-wrap break-all font-mono text-xs">
        {text}
      </pre>
    </div>
  );
}
