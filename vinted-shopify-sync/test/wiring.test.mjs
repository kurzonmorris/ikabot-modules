/**
 * Wiring smoke test: loads the service worker against a stubbed Chrome API and
 * drives it through the same messages the popup and options page send.
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { installChromeStub } from './chrome-stub.mjs';

const harness = installChromeStub();

// Imported after the stub is installed — the service worker registers its
// listeners at module load.
const { saveSettings, getSettings } = await import('../src/lib/storage.js');
const { MSG } = await import('../src/lib/constants.js');
await import('../src/background/service-worker.js');

test('defaults are safe: dry run on, no scheduled runs, no writes configured', async () => {
  const settings = await getSettings();
  assert.equal(settings.sync.dryRun, true);
  assert.equal(settings.sync.intervalMinutes, 0);
  assert.equal(settings.sync.createMissing, false);
  assert.equal(settings.shopify.accessToken, '');
});

test('saved settings merge over defaults instead of replacing them', async () => {
  await saveSettings({ shopify: { shopDomain: 'demo.myshopify.com' } });
  const settings = await getSettings();
  assert.equal(settings.shopify.shopDomain, 'demo.myshopify.com');
  assert.equal(settings.shopify.apiVersion, '2025-01', 'untouched keys survive a partial save');
  assert.equal(settings.sync.dryRun, true);
});

test('GET_STATE answers with state the popup can render', async () => {
  const response = await harness.dispatch({ type: MSG.GET_STATE });
  assert.equal(response.ok, true);
  assert.equal(response.state.running, false);
  assert.equal(response.settings.shopify.shopDomain, 'demo.myshopify.com');
  assert.equal(response.lastRun, null);
});

test('an interval schedules an alarm; zero clears it', async () => {
  await saveSettings({ sync: { intervalMinutes: 60 } });
  await new Promise((r) => setTimeout(r, 10)); // storage listener is async
  assert.equal(harness.alarms.get('scheduled-sync')?.periodInMinutes, 60);

  await saveSettings({ sync: { intervalMinutes: 0 } });
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(harness.alarms.has('scheduled-sync'), false);
});

test('the exported report never carries the Shopify access token', async () => {
  await saveSettings({ shopify: { accessToken: 'shpat_supersecret' } });
  const { report } = await harness.dispatch({ type: MSG.EXPORT_REPORT });

  assert.equal(report.settings.shopify.accessToken, '***redacted***');
  assert.ok(!JSON.stringify(report).includes('shpat_supersecret'));
  assert.ok(Array.isArray(report.logs));
});

test('a sync cannot start without Shopify credentials, and fails loudly', async () => {
  await saveSettings({ shopify: { accessToken: '', shopDomain: '' } });
  const response = await harness.dispatch({ type: MSG.RUN_SYNC, dryRun: true });

  assert.equal(response.ok, false);
  assert.match(response.error, /not configured/i);

  const state = await harness.dispatch({ type: MSG.GET_STATE });
  assert.equal(state.state.running, false, 'a failed run must not leave the worker stuck as running');
});
