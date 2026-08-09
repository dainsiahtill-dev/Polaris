import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Skeleton } from '@/app/components/ui/skeleton';
export function FileViewerSkeleton({ lines = 8 }) {
    return (_jsxs("div", { className: "soft-panel-subtle h-full flex flex-col", children: [_jsx("div", { className: "soft-panel-subtle px-4 py-3 border-b", children: _jsxs("div", { className: "flex items-center justify-between", children: [_jsxs("div", { className: "space-y-2", children: [_jsx(Skeleton, { className: "h-4 w-32" }), _jsx(Skeleton, { className: "h-3 w-48" })] }), _jsxs("div", { className: "flex items-center gap-2", children: [_jsx(Skeleton, { className: "h-4 w-16" }), _jsx(Skeleton, { className: "h-5 w-12" })] })] }) }), _jsx("div", { className: "flex-1 p-4", children: _jsx("div", { className: "space-y-2", children: Array.from({ length: lines }).map((_, i) => (_jsx(Skeleton, { className: "h-4 w-full" }, i))) }) })] }));
}
