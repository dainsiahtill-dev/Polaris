import { jsx as _jsx } from "react/jsx-runtime";
import React from 'react';
import { EnhancedAlert } from './EnhancedAlert';
export class ErrorBoundaryClass extends React.Component {
    constructor(props) {
        super(props);
        this.resetError = () => {
            this.setState({ hasError: false, error: null, errorInfo: null });
        };
        this.state = { hasError: false, error: null, errorInfo: null };
    }
    static getDerivedStateFromError(error) {
        return { hasError: true, error };
    }
    componentDidCatch(error, errorInfo) {
        this.setState({ error, errorInfo });
        if (this.props.onError) {
            this.props.onError(error, errorInfo);
        }
    }
    render() {
        if (this.state.hasError) {
            return (_jsx("div", { className: "min-h-screen bg-[var(--ink-indigo)] flex items-center justify-center p-4", children: _jsx("div", { className: "max-w-2xl w-full", children: _jsx(EnhancedAlert, { type: "error", title: "\u5E94\u7528\u53D1\u751F\u9519\u8BEF", message: this.state.error?.message || '未知错误', details: this.state.errorInfo?.componentStack || undefined, action: {
                            label: '重试',
                            onClick: this.resetError,
                        }, className: "mb-4" }) }) }));
        }
        return this.props.children;
    }
}
