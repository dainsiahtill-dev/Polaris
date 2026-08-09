import { jsx as _jsx } from "react/jsx-runtime";
import { cn } from "./utils";
function Skeleton({ className, ...props }) {
    return (_jsx("div", { "data-slot": "skeleton", className: cn("animate-pulse rounded-md border border-white/5 bg-slate-700/45", className), ...props }));
}
export { Skeleton };
