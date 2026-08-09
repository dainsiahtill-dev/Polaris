import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { ChevronLeftIcon, ChevronRightIcon, MoreHorizontalIcon, } from "lucide-react";
import { cn } from "./utils";
import { buttonVariants } from "./button";
function Pagination({ className, ...props }) {
    return (_jsx("nav", { role: "navigation", "aria-label": "pagination", "data-slot": "pagination", className: cn("mx-auto flex w-full justify-center", className), ...props }));
}
function PaginationContent({ className, ...props }) {
    return (_jsx("ul", { "data-slot": "pagination-content", className: cn("flex flex-row items-center gap-1", className), ...props }));
}
function PaginationItem({ ...props }) {
    return _jsx("li", { "data-slot": "pagination-item", ...props });
}
function PaginationLink({ className, isActive, size = "icon", ...props }) {
    return (_jsx("a", { "aria-current": isActive ? "page" : undefined, "data-slot": "pagination-link", "data-active": isActive, className: cn(buttonVariants({
            variant: isActive ? "outline" : "ghost",
            size,
        }), className), ...props }));
}
function PaginationPrevious({ className, ...props }) {
    return (_jsxs(PaginationLink, { "aria-label": "\u8F6C\u5230\u4E0A\u4E00\u9875", size: "default", className: cn("gap-1 px-2.5 sm:pl-2.5", className), ...props, children: [_jsx(ChevronLeftIcon, {}), _jsx("span", { className: "hidden sm:block", children: "\u4E0A\u4E00\u9875" })] }));
}
function PaginationNext({ className, ...props }) {
    return (_jsxs(PaginationLink, { "aria-label": "\u8F6C\u5230\u4E0B\u4E00\u9875", size: "default", className: cn("gap-1 px-2.5 sm:pr-2.5", className), ...props, children: [_jsx("span", { className: "hidden sm:block", children: "\u4E0B\u4E00\u9875" }), _jsx(ChevronRightIcon, {})] }));
}
function PaginationEllipsis({ className, ...props }) {
    return (_jsxs("span", { "aria-hidden": true, "data-slot": "pagination-ellipsis", className: cn("flex size-9 items-center justify-center", className), ...props, children: [_jsx(MoreHorizontalIcon, { className: "size-4" }), _jsx("span", { className: "sr-only", children: "\u66F4\u591A\u9875\u9762" })] }));
}
export { Pagination, PaginationContent, PaginationLink, PaginationItem, PaginationPrevious, PaginationNext, PaginationEllipsis, };
