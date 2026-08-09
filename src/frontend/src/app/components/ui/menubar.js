"use client";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import * as MenubarPrimitive from "@radix-ui/react-menubar";
import { CheckIcon, ChevronRightIcon, CircleIcon } from "lucide-react";
import { cn } from "./utils";
function Menubar({ className, ...props }) {
    return (_jsx(MenubarPrimitive.Root, { "data-slot": "menubar", className: cn("bg-background flex h-9 items-center gap-1 rounded-md border p-1 shadow-xs", className), ...props }));
}
function MenubarMenu({ ...props }) {
    return _jsx(MenubarPrimitive.Menu, { "data-slot": "menubar-menu", ...props });
}
function MenubarGroup({ ...props }) {
    return _jsx(MenubarPrimitive.Group, { "data-slot": "menubar-group", ...props });
}
function MenubarPortal({ ...props }) {
    return _jsx(MenubarPrimitive.Portal, { "data-slot": "menubar-portal", ...props });
}
function MenubarRadioGroup({ ...props }) {
    return (_jsx(MenubarPrimitive.RadioGroup, { "data-slot": "menubar-radio-group", ...props }));
}
function MenubarTrigger({ className, ...props }) {
    return (_jsx(MenubarPrimitive.Trigger, { "data-slot": "menubar-trigger", className: cn("focus:bg-accent focus:text-accent-foreground data-[state=open]:bg-accent data-[state=open]:text-accent-foreground flex items-center rounded-sm px-2 py-1 text-sm font-medium outline-hidden select-none", className), ...props }));
}
function MenubarContent({ className, align = "start", alignOffset = -4, sideOffset = 8, ...props }) {
    return (_jsx(MenubarPortal, { children: _jsx(MenubarPrimitive.Content, { "data-slot": "menubar-content", align: align, alignOffset: alignOffset, sideOffset: sideOffset, className: cn("bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[12rem] origin-(--radix-menubar-content-transform-origin) overflow-hidden rounded-md border p-1 shadow-md", className), ...props }) }));
}
function MenubarItem({ className, inset, variant = "default", ...props }) {
    return (_jsx(MenubarPrimitive.Item, { "data-slot": "menubar-item", "data-inset": inset, "data-variant": variant, className: cn("focus:bg-accent focus:text-accent-foreground data-[variant=destructive]:text-destructive data-[variant=destructive]:focus:bg-destructive/10 dark:data-[variant=destructive]:focus:bg-destructive/20 data-[variant=destructive]:focus:text-destructive data-[variant=destructive]:*:[svg]:!text-destructive [&_svg:not([class*='text-'])]:text-muted-foreground relative flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[inset]:pl-8 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4", className), ...props }));
}
function MenubarCheckboxItem({ className, children, checked, ...props }) {
    return (_jsxs(MenubarPrimitive.CheckboxItem, { "data-slot": "menubar-checkbox-item", className: cn("focus:bg-accent focus:text-accent-foreground relative flex cursor-default items-center gap-2 rounded-xs py-1.5 pr-2 pl-8 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4", className), checked: checked, ...props, children: [_jsx("span", { className: "pointer-events-none absolute left-2 flex size-3.5 items-center justify-center", children: _jsx(MenubarPrimitive.ItemIndicator, { children: _jsx(CheckIcon, { className: "size-4" }) }) }), children] }));
}
function MenubarRadioItem({ className, children, ...props }) {
    return (_jsxs(MenubarPrimitive.RadioItem, { "data-slot": "menubar-radio-item", className: cn("focus:bg-accent focus:text-accent-foreground relative flex cursor-default items-center gap-2 rounded-xs py-1.5 pr-2 pl-8 text-sm outline-hidden select-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4", className), ...props, children: [_jsx("span", { className: "pointer-events-none absolute left-2 flex size-3.5 items-center justify-center", children: _jsx(MenubarPrimitive.ItemIndicator, { children: _jsx(CircleIcon, { className: "size-2 fill-current" }) }) }), children] }));
}
function MenubarLabel({ className, inset, ...props }) {
    return (_jsx(MenubarPrimitive.Label, { "data-slot": "menubar-label", "data-inset": inset, className: cn("px-2 py-1.5 text-sm font-medium data-[inset]:pl-8", className), ...props }));
}
function MenubarSeparator({ className, ...props }) {
    return (_jsx(MenubarPrimitive.Separator, { "data-slot": "menubar-separator", className: cn("bg-border -mx-1 my-1 h-px", className), ...props }));
}
function MenubarShortcut({ className, ...props }) {
    return (_jsx("span", { "data-slot": "menubar-shortcut", className: cn("text-muted-foreground ml-auto text-xs tracking-widest", className), ...props }));
}
function MenubarSub({ ...props }) {
    return _jsx(MenubarPrimitive.Sub, { "data-slot": "menubar-sub", ...props });
}
function MenubarSubTrigger({ className, inset, children, ...props }) {
    return (_jsxs(MenubarPrimitive.SubTrigger, { "data-slot": "menubar-sub-trigger", "data-inset": inset, className: cn("focus:bg-accent focus:text-accent-foreground data-[state=open]:bg-accent data-[state=open]:text-accent-foreground flex cursor-default items-center rounded-sm px-2 py-1.5 text-sm outline-none select-none data-[inset]:pl-8", className), ...props, children: [children, _jsx(ChevronRightIcon, { className: "ml-auto h-4 w-4" })] }));
}
function MenubarSubContent({ className, ...props }) {
    return (_jsx(MenubarPrimitive.SubContent, { "data-slot": "menubar-sub-content", className: cn("bg-popover text-popover-foreground data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[side=bottom]:slide-in-from-top-2 data-[side=left]:slide-in-from-right-2 data-[side=right]:slide-in-from-left-2 data-[side=top]:slide-in-from-bottom-2 z-50 min-w-[8rem] origin-(--radix-menubar-content-transform-origin) overflow-hidden rounded-md border p-1 shadow-lg", className), ...props }));
}
export { Menubar, MenubarPortal, MenubarMenu, MenubarTrigger, MenubarContent, MenubarGroup, MenubarSeparator, MenubarLabel, MenubarItem, MenubarShortcut, MenubarCheckboxItem, MenubarRadioGroup, MenubarRadioItem, MenubarSub, MenubarSubTrigger, MenubarSubContent, };
