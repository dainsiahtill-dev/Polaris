import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, } from "react";
import { createPortal } from "react-dom";
export function ContextMenu({ x, y, items, onClose, title }) {
    const menuRef = useRef(null);
    const [position, setPosition] = useState({ x, y });
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (menuRef.current && !menuRef.current.contains(event.target)) {
                onClose();
            }
        };
        const handleKeyDown = (event) => {
            if (event.key === "Escape") {
                onClose();
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        document.addEventListener("keydown", handleKeyDown);
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
            document.removeEventListener("keydown", handleKeyDown);
        };
    }, [onClose]);
    useLayoutEffect(() => {
        const menu = menuRef.current;
        if (!menu) {
            setPosition({ x, y });
            return;
        }
        const margin = 8;
        const rect = menu.getBoundingClientRect();
        const maxX = Math.max(margin, window.innerWidth - rect.width - margin);
        const maxY = Math.max(margin, window.innerHeight - rect.height - margin);
        setPosition({
            x: Math.min(Math.max(x, margin), maxX),
            y: Math.min(Math.max(y, margin), maxY),
        });
    }, [x, y, items.length, title]);
    if (typeof document === "undefined") {
        return null;
    }
    const style = {
        top: position.y,
        left: position.x,
    };
    return createPortal(_jsxs("div", { ref: menuRef, "data-testid": "llm-visual-context-menu", className: "fixed z-[9999] min-w-[180px] overflow-hidden soft-panel rounded-lg p-1 animate-in fade-in zoom-in-95 duration-100", style: style, onContextMenu: (e) => e.preventDefault(), children: [title && (_jsxs("div", { className: "flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs font-semibold text-text-dim", children: [_jsx("span", { children: title }), _jsx("button", { type: "button", onClick: onClose, className: "hover:text-white", children: _jsx(X, { size: 12 }) })] })), _jsx("div", { className: "p-1", children: items.map((item, index) => {
                    const Icon = item.icon;
                    const isDanger = item.variant === "danger";
                    return (_jsxs("button", { type: "button", onClick: () => {
                            if (!item.disabled) {
                                item.action();
                                onClose();
                            }
                        }, disabled: item.disabled, "data-testid": `llm-visual-context-menu-item-${item.label}`, className: `
                flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors
                ${item.disabled
                            ? "cursor-not-allowed opacity-50 text-text-dim"
                            : isDanger
                                ? "text-red-400 hover:bg-red-500/20 hover:text-red-200"
                                : "text-text-main hover:bg-white/10 hover:text-white"}
              `, children: [Icon && (_jsx(Icon, { size: 14, className: isDanger ? "text-red-400" : "text-slate-400" })), _jsx("span", { children: item.label })] }, index));
                }) })] }), document.body);
}
