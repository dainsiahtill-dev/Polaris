/**
 * Vitest 测试环境设置
 */

import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

type TestGlobals = {
    vi?: unknown;
};

const globals = globalThis as TestGlobals;

globals.vi = globals.vi ?? vi;

if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
}

// React Flow 及虚拟化列表在 jsdom 中需要 ResizeObserver；jsdom 本身不提供。
const globalWithResizeObserver = globalThis as { ResizeObserver?: unknown };
if (typeof globalWithResizeObserver.ResizeObserver === 'undefined') {
    globalWithResizeObserver.ResizeObserver = class {
        observe() {}
        unobserve() {}
        disconnect() {}
    };
}

// 每次测试后自动清理
afterEach(() => {
    cleanup();
});
