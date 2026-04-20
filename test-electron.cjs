const { chromium } = require('playwright');

(async () => {
  // Launch Electron app
  const browser = await chromium.launch({
    headless: false,
    args: ['--remote-debugging-port=9222']
  });

  // Wait for app to connect
  await new Promise(r => setTimeout(r, 5000));

  const context = browser.contexts()[0];
  const page = context.pages()[0];

  // Wait for the app to load
  await page.waitForLoadState('networkidle');
  console.log('Page loaded:', page.url());

  // Check if Polaris is available
  const polaris = await page.evaluate(() => {
    return window.polaris;
  });
  console.log('Polaris object:', polaris);

  await browser.close();
})();
