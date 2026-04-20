import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
    plugins: [react()],
    test: {
        globals: true,
        environment: 'jsdom',
        setupFiles: './src/frontend/src/test/setup.ts',
        include: [
            'src/frontend/src/**/*.test.{ts,tsx}',
            'src/frontend/src/**/*.spec.{ts,tsx}',
        ],
        exclude: [
            'tests/**',
            'node_modules/**',
            'dist/**',
        ],
        coverage: {
            provider: 'v8',
            reporter: ['text', 'json', 'html'],
            exclude: [
                'node_modules/',
                'src/frontend/src/test/',
                '**/*.test.{ts,tsx}',
                '**/*.spec.{ts,tsx}',
            ],
        },
    },
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src/frontend/src'),
        },
    },
});
