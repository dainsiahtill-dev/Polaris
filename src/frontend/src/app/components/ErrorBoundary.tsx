import React from 'react';
import { EnhancedAlert } from './EnhancedAlert';

interface ErrorBoundaryProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  onError?: (error: Error, errorInfo: React.ErrorInfo) => void;
}

interface ErrorState {
  hasError: boolean;
  error: Error | null;
  errorInfo: React.ErrorInfo | null;
}

export class ErrorBoundaryClass extends React.Component<ErrorBoundaryProps, ErrorState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorState> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({ error, errorInfo });
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  resetError = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-screen bg-[var(--ink-indigo)] flex items-center justify-center p-4">
          <div className="max-w-2xl w-full">
            <EnhancedAlert
              type="error"
              title="应用发生错误"
              message={this.state.error?.message || '未知错误'}
              details={this.state.errorInfo?.componentStack || undefined}
              action={{
                label: '重试',
                onClick: this.resetError,
              }}
              className="mb-4"
            />
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
