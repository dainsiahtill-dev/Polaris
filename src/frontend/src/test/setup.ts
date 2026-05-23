/**
 * Vitest 测试环境设置
 */

import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

type TestGlobals = {
    vi?: unknown;
    jest?: unknown;
};

const globals = globalThis as TestGlobals;

// Compatibility shim for legacy tests still using jest.fn().
globals.vi = globals.vi ?? vi;
globals.jest = globals.vi;

if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
}

// 每次测试后自动清理
afterEach(() => {
    cleanup();
});
