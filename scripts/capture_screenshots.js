/**
 * Playwright script to capture verified screenshots of SentinelX UI
 */
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function main() {
  const screenshotsDir = path.resolve(__dirname, '../docs/screenshots');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir, { recursive: true });
  }

  console.log('Launching browser (channel: msedge)...');
  const browser = await chromium.launch({
    channel: 'msedge',
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });

  const page = await context.newPage();
  const baseUrl = 'http://127.0.0.1:8000';

  console.log(`Navigating to ${baseUrl}...`);
  await page.goto(baseUrl, { waitUntil: 'networkidle' });

  // 1. Login Modal Screenshot
  console.log('1. Capturing login modal...');
  await page.click('#btn-open-login');
  await page.waitForSelector('#login-modal.active', { state: 'visible', timeout: 5000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(screenshotsDir, 'login.png') });

  // Authenticate
  console.log('Logging in...');
  const adminEmail = process.env.ADMIN_EMAIL || 'admin@sentinelx.local';
  const adminPassword = process.env.ADMIN_PASSWORD || 'AdminSecret2026!';
  await page.fill('#login-email', adminEmail);
  await page.fill('#login-password', adminPassword);
  await page.click('#login-form button[type="submit"]');

  await page.waitForSelector('#auth-logged-in', { state: 'visible', timeout: 8000 });
  await page.waitForTimeout(1000);

  // 2. Dashboard Overview Screenshot
  console.log('2. Capturing executive dashboard...');
  await page.screenshot({ path: path.join(screenshotsDir, 'dashboard.png') });

  // 3. Transactions with Risk Drawer Screenshot
  console.log('3. Capturing transactions with risk drawer...');
  await page.click('button[data-tab="tab-transactions"]');
  await page.waitForSelector('#tx-table-body tr', { timeout: 8000 });
  await page.waitForTimeout(800);

  // Find first inspect button and click
  const inspectBtn = await page.$('#tx-table-body button.btn');
  if (inspectBtn) {
    await inspectBtn.click();
    await page.waitForSelector('#detail-drawer.open', { state: 'visible', timeout: 5000 });
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(screenshotsDir, 'transactions_risk_drawer.png') });

  // Close drawer
  await page.evaluate(() => { if (typeof closeDrawer === 'function') closeDrawer(); });
  await page.waitForTimeout(400);

  // 4. Alerts Triage Screenshot
  console.log('4. Capturing alerts triage board...');
  await page.click('button[data-tab="tab-alerts"]');
  await page.waitForSelector('#alerts-table-body tr', { timeout: 8000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(screenshotsDir, 'alerts.png') });

  // 5. Investigation Timeline Screenshot
  console.log('5. Capturing investigation case timeline...');
  await page.click('button[data-tab="tab-investigations"]');
  await page.waitForSelector('#inv-table-body tr', { timeout: 8000 });
  await page.waitForTimeout(800);

  const inspectCaseBtn = await page.$('#inv-table-body button.btn');
  if (inspectCaseBtn) {
    await inspectCaseBtn.click();
    await page.waitForSelector('#detail-drawer.open', { state: 'visible', timeout: 5000 });
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(screenshotsDir, 'investigation_timeline.png') });

  // Close drawer
  await page.evaluate(() => { if (typeof closeDrawer === 'function') closeDrawer(); });
  await page.waitForTimeout(400);

  // 6. Network Graph Screenshot
  console.log('6. Capturing fraud network graph...');
  await page.click('button[data-tab="tab-network"]');
  await page.waitForSelector('#networkCanvas', { timeout: 8000 });
  // Wait 2 seconds for force-directed layout simulation to settle
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(screenshotsDir, 'network_graph.png') });

  await browser.close();
  console.log('All 6 screenshots captured successfully!');
}

main().catch(err => {
  console.error('Screenshot error:', err);
  process.exit(1);
});

