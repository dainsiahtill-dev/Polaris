import { test, expect } from '@playwright/test';

const BASE_URL = process.env.FRONTEND_URL || 'http://127.0.0.1:5173';

test.describe('ContextOS realtime view visual audit', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE_URL}/`);
    // Wait for the main app shell to render.
    await page.waitForSelector('[data-testid="control-panel-enter-contextos"]', { timeout: 10_000 });
  });

  test('empty state: no bench strip pollution and consolidated header', async ({ page }) => {
    // Open ContextOS realtime view.
    await page.click('[data-testid="control-panel-enter-contextos"]');
    await page.waitForSelector('[data-testid="contextos-workspace"]', { timeout: 10_000 });

    // Critical: Factory Bench status strip must not appear inside ContextOS.
    await expect(page.locator('[data-testid="bench-status-strip"]')).toHaveCount(0);

    // Consolidated header elements.
    await expect(page.locator('[data-testid="contextos-resource-chip"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="contextos-telemetry-freshness"]')).toHaveCount(1);

    // Pipeline and role cards remain visible.
    for (const id of ['request', 'truthlog', 'working_mem', 'projection', 'role_signal', 'budget', 'prompt', 'llm']) {
      await expect(page.locator(`[data-testid="contextos-stage-${id}"]`)).toHaveCount(1);
    }
    for (const id of ['pm', 'architect', 'chief_engineer', 'director', 'qa']) {
      await expect(page.locator(`[data-testid="contextos-role-${id}"]`)).toHaveCount(1);
    }

    // Removed noise sources.
    await expect(page.locator('text=组件健康')).toHaveCount(0);
    await expect(page.locator('text=Outcome Feedback Loop')).toHaveCount(0);
    await expect(page.locator('text=按模式分布')).toHaveCount(0);

    await page.screenshot({ path: 'playwright-report/contextos-audit/contextos-empty-state.png', fullPage: false });
  });

  test('role detail panel opens and stays readable', async ({ page }) => {
    await page.click('[data-testid="control-panel-enter-contextos"]');
    await page.waitForSelector('[data-testid="contextos-workspace"]', { timeout: 10_000 });

    await page.click('[data-testid="contextos-role-pm"]');
    await page.waitForSelector('[data-testid="contextos-role-panel-pm"]', { timeout: 5_000 });

    await expect(page.locator('[data-testid="contextos-role-panel-pm"]')).toContainText('TruthLog');
    await expect(page.locator('[data-testid="contextos-role-panel-pm"]')).toContainText('ProjectionEngine');
    await expect(page.locator('[data-testid="contextos-role-panel-pm"]')).toContainText('ReceiptStore');

    await page.screenshot({ path: 'playwright-report/contextos-audit/contextos-role-pm-detail.png', fullPage: false });
  });
});
