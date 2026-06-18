import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for the standalone renderer dev server.
 * Used for visual audits of the React UI served by Vite at http://127.0.0.1:5173
 */
export default defineConfig({
  testDir: './src/frontend/e2e',
  outputDir: './playwright-report/contextos-audit',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { outputFolder: './playwright-report/contextos-audit-html' }]],
  use: {
    baseURL: process.env.FRONTEND_URL || 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
});
