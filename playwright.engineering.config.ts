import { defineConfig, devices } from '@playwright/test';
import { homedir } from 'node:os';
import { resolve } from 'node:path';
import { parseEngineeringAcceptanceUrl } from './src/frontend/e2e/engineering-fixture';
const binding = parseEngineeringAcceptanceUrl(process.env.ENGINEERING_ACCEPTANCE_URL);
const reportDir = resolve(process.env.ENGINEERING_ACCEPTANCE_REPORT_DIR || resolve(homedir(), '.polaris/audit_archives/engineering-browser'));
const timeout = Number(process.env.ENGINEERING_ACCEPTANCE_TIMEOUT_MS || 5_400_000);
if (!Number.isSafeInteger(timeout) || timeout <= 0) throw new Error('Invalid ENGINEERING_ACCEPTANCE_TIMEOUT_MS');
export default defineConfig({
  testDir: './src/frontend/e2e', testMatch: 'engineering-run.spec.ts', outputDir: resolve(reportDir, 'artifacts'),
  fullyParallel: false, workers: 1, retries: 0, forbidOnly: Boolean(process.env.CI), timeout,
  reporter: [['line'], ['json', { outputFile: resolve(reportDir, 'results.json') }]],
  use: { baseURL: binding.url, headless: true, trace: 'on', screenshot: 'on', actionTimeout: 30_000 },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } }],
});
