/**
 * Service worker: the only place that talks to both platforms.
 *
 * MV3 workers are killed when idle, so nothing durable is kept in module scope —
 * run state is transient by design and everything worth keeping goes to storage.
 */

import { MSG, DEFAULT_SETTINGS } from '../lib/constants.js';
import { getSettings, saveSettings, getLastRun, getLinks, getSnapshot } from '../lib/storage.js';
import { logger, getLogs, clearLogs } from '../lib/logger.js';
import { SyncRun } from '../lib/sync-engine.js';
import { VintedAdapter } from '../adapters/vinted.js';
import { ShopifyAdapter } from '../adapters/shopify.js';

const ALARM_NAME = 'scheduled-sync';

/** Transient run state, mirrored to listeners via STATE_CHANGED. */
let current = { running: false, phase: 'idle', progress: null, error: null };
let activeRun = null;

function setState(patch) {
  current = { ...current, ...patch };
  chrome.runtime.sendMessage({ type: MSG.STATE_CHANGED, state: current }).catch(() => {});
}

chrome.runtime.onInstalled.addListener(async (details) => {
  const settings = await saveSettings({}); // materialises defaults on first install
  await logger.info(`Extension ${details.reason} (v${chrome.runtime.getManifest().version})`);
  await rescheduleAlarm(settings);
  if (details.reason === 'install') chrome.runtime.openOptionsPage();
});

chrome.runtime.onStartup.addListener(async () => {
  await rescheduleAlarm(await getSettings());
});

chrome.storage.onChanged.addListener(async (changes, area) => {
  if (area === 'local' && changes.settings) await rescheduleAlarm(await getSettings());
});

async function rescheduleAlarm(settings) {
  await chrome.alarms.clear(ALARM_NAME);
  const minutes = Number(settings.sync.intervalMinutes) || 0;
  if (minutes <= 0) return;
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: minutes, delayInMinutes: minutes });
  logger.debug(`Scheduled sync every ${minutes} min`);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) startSync({ source: 'schedule' });
});

async function startSync({ source = 'manual', dryRunOverride } = {}) {
  if (current.running) return { ok: false, error: 'A sync is already running.' };

  setState({ running: true, phase: 'starting', error: null, progress: null });
  activeRun = new SyncRun({ onProgress: (progress) => setState({ phase: progress.phase, progress }) });

  try {
    const lastRun = await activeRun.run({ dryRunOverride });
    setState({ running: false, phase: 'idle', progress: null });
    return { ok: true, lastRun };
  } catch (error) {
    const message = String(error?.message ?? error);
    await logger.error(`Run aborted (${source}): ${message}`);
    setState({ running: false, phase: 'idle', error: message });
    return { ok: false, error: message };
  } finally {
    activeRun = null;
  }
}

const handlers = {
  async [MSG.GET_STATE]() {
    return {
      ok: true,
      state: current,
      settings: await getSettings(),
      lastRun: await getLastRun(),
      links: Object.keys(await getLinks()).length,
      snapshotAt: (await getSnapshot()).takenAt,
      version: chrome.runtime.getManifest().version,
    };
  },

  [MSG.RUN_SYNC](message) {
    return startSync({ source: 'popup', dryRunOverride: message.dryRun });
  },

  [MSG.CANCEL_SYNC]() {
    activeRun?.cancel();
    return { ok: true };
  },

  async [MSG.TEST_SHOPIFY]() {
    const settings = await getSettings();
    const result = await new ShopifyAdapter(settings).testConnection();
    // First successful connect picks a location so inventory writes have a home.
    if (!settings.shopify.locationId && result.suggestedLocationId) {
      await saveSettings({ shopify: { locationId: result.suggestedLocationId } });
    }
    await logger.info(`Shopify connected: ${result.shop} (${result.currency})`, result.locations);
    return { ok: true, result };
  },

  async [MSG.TEST_VINTED]() {
    const result = await new VintedAdapter(await getSettings()).testConnection();
    await logger.info(`Vinted connected as ${result.username || result.userId}`, { via: result.via });
    return { ok: true, result };
  },

  async [MSG.GET_LOGS]() {
    return { ok: true, logs: await getLogs() };
  },

  async [MSG.CLEAR_LOGS]() {
    await clearLogs();
    return { ok: true };
  },

  /** Self-contained JSON dump — the handover point for any external monitor. */
  async [MSG.EXPORT_REPORT]() {
    const settings = await getSettings();
    return {
      ok: true,
      report: {
        generatedAt: new Date().toISOString(),
        version: chrome.runtime.getManifest().version,
        settings: redact(settings),
        state: current,
        lastRun: await getLastRun(),
        links: await getLinks(),
        logs: await getLogs(),
      },
    };
  },
};

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = handlers[message?.type];
  if (!handler) return false;

  Promise.resolve()
    .then(() => handler(message))
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: String(error?.message ?? error) }));
  return true;
});

/** Never let the Admin token reach an export, the clipboard or a log line. */
function redact(settings) {
  const copy = structuredClone({ ...DEFAULT_SETTINGS, ...settings });
  copy.shopify.accessToken = settings.shopify.accessToken ? '***redacted***' : '';
  return copy;
}
