/**
 * Vitest 测试环境设置
 */

import '@testing-library/jest-dom';
import { cleanup } from '@testing-library/react';

type TestGlobals = {
    afterEach?: (fn: () => void) => void;
    vi?: unknown;
    jest?: unknown;
};

const globals = globalThis as TestGlobals;

// Compatibility shim for legacy tests still using jest.fn().
if (globals.vi) {
    globals.jest = globals.vi;
}

// 每次测试后自动清理（只在 vitest runtime 内注册）
if (typeof globals.afterEach === 'function') {
    globals.afterEach(() => {
        cleanup();
    });
}
