import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { engineeringBootstrap, engineeringEvents, engineeringSnapshotReads, inspectEngineeringNetwork, observeEngineeringNetwork, parseEngineeringAcceptanceUrl, type EngineeringNetworkAudit } from './engineering-fixture';
const binding = parseEngineeringAcceptanceUrl(process.env.ENGINEERING_ACCEPTANCE_URL);
const component = process.env.ENGINEERING_ACCEPTANCE_COMPONENT === 'synthetic_native';
if (process.env.ENGINEERING_ACCEPTANCE_COMPONENT && !component) throw new Error('Unknown component fixture mode');
const runPath = `/v2/engineering/runs/${encodeURIComponent(binding.sessionId)}`;
function scopedEvents(audit: EngineeringNetworkAudit) { return engineeringEvents(audit.sockets.flatMap((socket) => socket.frames), binding); }
async function attachAudit(testInfo: TestInfo, audit: EngineeringNetworkAudit) {
  await testInfo.attach('engineering-network-audit', { body: JSON.stringify({ component_fixture: component, model_acceptance: false, ...audit }, null, 2), contentType: 'application/json' });
}
async function openBoundRun(page: Page, audit: EngineeringNetworkAudit) {
  const initial = page.waitForResponse((response) => new URL(response.url()).pathname === runPath && response.request().method() === 'GET');
  await page.goto(binding.url);
  const response = await initial;
  expect(response.status()).toBe(200);
  const snapshot = await response.json();
  expect(snapshot).toMatchObject({ session_id: binding.sessionId, workspace: binding.workspace, engine_version: 'engineering_v2' });
  await expect(page.getByTestId('engineering-run-workspace')).toBeVisible();
  await expect(page.getByText(binding.sessionId, { exact: true })).toBeVisible();
  await expect.poll(() => audit.sockets.filter((socket) => new URL(socket.url).pathname === '/v2/ws/runtime').length).toBe(1);
  await expect.poll(() => engineeringBootstrap(audit, binding).ready, { timeout: 5000, message: 'Initial hydration and observed first-connection freshness must both settle' }).toBe(true);
  const bootstrap = engineeringBootstrap(audit, binding);
  audit.bootstrap = bootstrap;
  return { watermark: bootstrap.maxSqlSeq!, reads: bootstrap.readCount };
}
async function waitForActualPush(page: Page, audit: EngineeringNetworkAudit, watermark: number, timeout: number) {
  await expect.poll(() => scopedEvents(audit).filter((event) => event.seq > watermark && !['run_updated', 'message'].includes(event.kind)).length, { timeout }).toBeGreaterThan(0);
  const event = scopedEvents(audit).find((row) => row.seq > watermark && !['run_updated', 'message'].includes(row.kind))!;
  await expect(page.getByText(`#${event.seq}`, { exact: true })).toBeVisible();
  const subscriptions = audit.sockets.flatMap((socket) => socket.sent || []).filter((frame) => frame && typeof frame === 'object' && (frame as Record<string, unknown>).type === 'SUBSCRIBE');
  expect(JSON.stringify(subscriptions)).toContain('event.engineering');
}
async function openActualContext(page: Page) {
  const contextResponse = page.waitForResponse((response) => /\/v2\/context\/[a-f0-9]{24}\/final-request$/.test(new URL(response.url()).pathname));
  await page.getByRole('button', { name: '查看最终请求', exact: true }).first().click();
  const response = await contextResponse;
  expect(response.status()).toBe(200);
  expect(new URL(response.url()).searchParams.get('workspace')).toBe(binding.workspace);
  const request = await response.json();
  expect(Array.isArray(request.messages)).toBe(true);
  expect(Array.isArray(request.tools)).toBe(true);
  await expect(page.getByTestId('contextos-viewer-modal')).toBeVisible();
  await expect(page.getByTestId('contextos-viewer-tab-final-request')).toHaveAttribute('aria-pressed', 'true');
  await page.getByTestId('contextos-viewer-close').click();
}
if (component) {
  test('synthetic native component: actual WS, readable context, unavailable refresh and real cancel', async ({ page }, testInfo) => {
    testInfo.annotations.push({ type: 'boundary', description: 'Synthetic native HTTP provider; actual public service/SQLite/tools/NATS/WS/browser. Not real-model or Bench acceptance.' });
    const audit = observeEngineeringNetwork(page);
    try {
      const bootstrap = await openBoundRun(page, audit);
      const releaseFile = process.env.ENGINEERING_ACCEPTANCE_COMPONENT_RELEASE;
      if (releaseFile) {
        if (resolve(releaseFile) !== resolve(binding.workspace, '..', 'release-provider')) throw new Error('Component release must be an owned fixture control file outside its target workspace');
        await writeFile(releaseFile, 'Browser hydrated; release synthetic provider.\n', { encoding: 'utf-8', flag: 'wx' });
      }
      await waitForActualPush(page, audit, bootstrap.watermark, Math.max(1000, testInfo.timeout - 30_000));
      await expect(page.getByText('synthetic native component output', { exact: true })).toBeVisible();
      for (const role of ['pm', 'chief_engineer', 'qa']) await expect(page.getByTestId(`engineering-role-${role}`)).toContainText('未调用');
      await expect(page.getByTestId('engineering-qa-verdict')).toContainText('未调用');
      await openActualContext(page);
      expect(engineeringSnapshotReads(audit, binding)).toBe(bootstrap.reads);
      // A bounded test wait audits absence of product polling; it is not a refresh mechanism.
      await page.waitForTimeout(1000);
      expect(engineeringSnapshotReads(audit, binding)).toBe(bootstrap.reads);
      const match = (url: URL) => url.pathname === runPath;
      await page.route(match, (route) => route.abort('failed'));
      await page.getByRole('button', { name: '刷新工程快照' }).click();
      await expect(page.getByTestId('engineering-verification')).toContainText('未知（快照不可用）');
      expect(engineeringSnapshotReads(audit, binding)).toBe(bootstrap.reads + 1);
      await page.unroute(match);
      await page.getByRole('button', { name: '刷新工程快照' }).click();
      await expect(page.getByTestId('engineering-verification')).not.toContainText('快照不可用');
      expect(engineeringSnapshotReads(audit, binding)).toBe(bootstrap.reads + 2);
      await page.getByRole('button', { name: '取消', exact: true }).click();
      await expect(page.getByRole('status')).toContainText('已取消');
      await expect.poll(() => scopedEvents(audit).some((event) => event.kind === 'run_updated' && event.payload.status === 'cancelled')).toBe(true);
      expect(inspectEngineeringNetwork(audit, binding, bootstrap.reads + 2)).toEqual([]);
      expect(audit.failedRequests).toHaveLength(1);
      await page.screenshot({ path: testInfo.outputPath('component-cancelled.png'), fullPage: true });
    } finally { await attachAudit(testInfo, audit); }
  });
} else {
  test('read-only bound real run: actual WS progress and current ContextOS request', async ({ page }, testInfo) => {
    testInfo.annotations.push({ type: 'boundary', description: 'Read-only observer: never starts/cancels/resumes a run or repairs its target. Model delivery is judged by owner evidence separately.' });
    const audit = observeEngineeringNetwork(page);
    try {
      const bootstrap = await openBoundRun(page, audit);
      await waitForActualPush(page, audit, bootstrap.watermark, Math.max(1000, testInfo.timeout - 30_000));
      await openActualContext(page);
      await page.waitForTimeout(1000);
      expect(inspectEngineeringNetwork(audit, binding, bootstrap.reads)).toEqual([]);
      expect(audit.requests.filter((request) => request.method !== 'GET' && new URL(request.url).pathname.startsWith('/v2/engineering/runs'))).toEqual([]);
      expect(audit.failedRequests).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath('bound-run-progress.png'), fullPage: true });
    } finally { await attachAudit(testInfo, audit); }
  });
}
