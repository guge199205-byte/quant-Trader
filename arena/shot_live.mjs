import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true });
const p = await b.newPage({ viewport: { width: 1440, height: 2600 } });
await p.goto('http://127.0.0.1:8092/', { waitUntil: 'networkidle', timeout: 30000 });
// 切到实盘 tab
const tab = p.locator('text=实盘').first();
await tab.click();
await p.waitForTimeout(4500); // 等轮询数据回来
await p.screenshot({ path: '/tmp/live_tab.png', fullPage: true });
await b.close();
console.log('shot done');
