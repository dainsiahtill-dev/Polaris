
import { X, Minus, Maximize2, Square } from 'lucide-react';
import { useState, useEffect } from 'react';
import { devLogger } from '@/app/utils/devLogger';

export function WindowControls() {
    const [isHovered, setIsHovered] = useState(false);
    const [isMaximized, setIsMaximized] = useState(false);

    useEffect(() => {
        // Get initial state
        const loadState = async () => {
            try {
                const state = await window.polaris?.windowControl?.getState?.();
                if (state) {
                    setIsMaximized(state.maximized);
                }
            } catch (error) {
                devLogger.warn('Failed to get window state:', error);
            }
        };
        loadState();
    }, []);

    const handleMaximize = async () => {
        try {
            const newState = await window.polaris?.windowControl?.maximize?.();
            if (typeof newState === 'boolean') {
                setIsMaximized(newState);
            }
        } catch (error) {
            devLogger.error('Failed to toggle maximize:', error);
        }
    };

    const handleClose = () => {
        window.polaris?.windowControl?.close?.();
    };

    const handleMinimize = () => {
        window.polaris?.windowControl?.minimize?.();
    };

    return (
        <div
            className="flex items-center gap-2 px-2 no-drag group"
            onMouseEnter={() => setIsHovered(true)}
            onMouseLeave={() => setIsHovered(false)}
        >
            <button
                onClick={handleClose}
                className="size-3 rounded-full bg-[#ff5f56] border border-[#e0443e] flex items-center justify-center text-[#4a0002]/70 hover:text-[#4a0002] transition-colors shadow-inner"
                aria-label="Close"
            >
                {isHovered && <X className="size-2" strokeWidth={3} />}
            </button>

            <button
                onClick={handleMinimize}
                className="size-3 rounded-full bg-[#ffbd2e] border border-[#dea123] flex items-center justify-center text-[#5c3c00]/70 hover:text-[#5c3c00] transition-colors shadow-inner"
                aria-label="Minimize"
            >
                {isHovered && <Minus className="size-2" strokeWidth={3} />}
            </button>

            <button
                onClick={handleMaximize}
                className="size-3 rounded-full bg-[#27c93f] border border-[#1aab29] flex items-center justify-center text-[#0a4d13]/70 hover:text-[#0a4d13] transition-colors shadow-inner"
                aria-label={isMaximized ? "Restore" : "Maximize"}
            >
                {isHovered && (
                    isMaximized ?
                        <Square className="size-2" strokeWidth={3} /> :
                        <Maximize2 className="size-2" strokeWidth={3} />
                )}
            </button>
        </div>
    );
}
