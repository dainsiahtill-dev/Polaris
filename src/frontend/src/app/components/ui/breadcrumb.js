import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Slot } from "@radix-ui/react-slot";
import { ChevronRight, MoreHorizontal } from "lucide-react";
import { cn } from "./utils";
function Breadcrumb({ ...props }) {
    return _jsx("nav", { "aria-label": "breadcrumb", "data-slot": "breadcrumb", ...props });
}
function BreadcrumbList({ className, ...props }) {
    return (_jsx("ol", { "data-slot": "breadcrumb-list", className: cn("text-muted-foreground flex flex-wrap items-center gap-1.5 text-sm break-words sm:gap-2.5", className), ...props }));
}
function BreadcrumbItem({ className, ...props }) {
    return (_jsx("li", { "data-slot": "breadcrumb-item", className: cn("inline-flex items-center gap-1.5", className), ...props }));
}
function BreadcrumbLink({ asChild, className, ...props }) {
    const Comp = asChild ? Slot : "a";
    return (_jsx(Comp, { "data-slot": "breadcrumb-link", className: cn("hover:text-foreground transition-colors", className), ...props }));
}
function BreadcrumbPage({ className, ...props }) {
    return (_jsx("span", { "data-slot": "breadcrumb-page", role: "link", "aria-disabled": "true", "aria-current": "page", className: cn("text-foreground font-normal", className), ...props }));
}
function BreadcrumbSeparator({ children, className, ...props }) {
    return (_jsx("li", { "data-slot": "breadcrumb-separator", role: "presentation", "aria-hidden": "true", className: cn("[&>svg]:size-3.5", className), ...props, children: children ?? _jsx(ChevronRight, {}) }));
}
function BreadcrumbEllipsis({ className, ...props }) {
    return (_jsxs("span", { "data-slot": "breadcrumb-ellipsis", role: "presentation", "aria-hidden": "true", className: cn("flex size-9 items-center justify-center", className), ...props, children: [_jsx(MoreHorizontal, { className: "size-4" }), _jsx("span", { className: "sr-only", children: "\u66F4\u591A" })] }));
}
export { Breadcrumb, BreadcrumbList, BreadcrumbItem, BreadcrumbLink, BreadcrumbPage, BreadcrumbSeparator, BreadcrumbEllipsis, };
