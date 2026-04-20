import { LucideIcon, X } from 'lucide-react';
import { useEffect, useRef } from 'react';

export interface ContextMenuItem {
  label: string;
  icon?: LucideIcon;
  action: () => void;
  variant?: 'default' | 'danger' | 'warning';
  disabled?: boolean;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
  title?: string;
}

export function ContextMenu({ x, y, items, onClose, title }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    
    // Close on escape key
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);
    
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [onClose]);

  // Adjust position to keep within viewport
  const style = {
    top: y,
    left: x,
  };

  return (
    <div
      ref={menuRef}
      className="absolute z-50 min-w-[180px] overflow-hidden rounded-lg border border-white/10 bg-black/90 p-1 shadow-[0_0_24px_rgba(0,0,0,0.5)] backdrop-blur-md animate-in fade-in zoom-in-95 duration-100"
      style={style}
      onContextMenu={(e) => e.preventDefault()}
    >
      {title && (
        <div className="flex items-center justify-between border-b border-white/10 px-3 py-2 text-xs font-semibold text-text-dim">
          <span>{title}</span>
          <button onClick={onClose} className="hover:text-white">
            <X size={12} />
          </button>
        </div>
      )}
      <div className="p-1">
        {items.map((item, index) => {
          const Icon = item.icon;
          const isDanger = item.variant === 'danger';
          
          return (
            <button
              key={index}
              onClick={() => {
                if (!item.disabled) {
                  item.action();
                  onClose();
                }
              }}
              disabled={item.disabled}
              className={`
                flex w-full items-center gap-2 rounded px-2 py-1.5 text-xs transition-colors
                ${item.disabled 
                  ? 'cursor-not-allowed opacity-50 text-text-dim' 
                  : isDanger 
                    ? 'text-red-400 hover:bg-red-500/20 hover:text-red-200' 
                    : 'text-text-main hover:bg-white/10 hover:text-white'
                }
              `}
            >
              {Icon && <Icon size={14} className={isDanger ? 'text-red-400' : 'text-cyan-400'} />}
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
