/**
 * Ring-buffer logger.
 *
 * Every step of a sync run lands here so the run can be audited after the fact
 * (popup > Activity, or "Export report" for a machine-readable dump). Writes are
 * debounced because a run can emit hundreds of entries in a couple of seconds.
 */

import { STORAGE_KEYS, LOG_LEVEL, MSG } from './constants.js';
import { getSettings } from './storage.js';

let buffer = null;
let flushTimer = null;
let maxEntries = 500;

async function load() {
  if (buffer) return buffer;
  const raw = await chrome.storage.local.get(STORAGE_KEYS.LOGS);
  buffer = raw[STORAGE_KEYS.LOGS] ?? [];
  return buffer;
}

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(async () => {
    flushTimer = null;
    await chrome.storage.local.set({ [STORAGE_KEYS.LOGS]: buffer ?? [] });
  }, 250);
}

/** Fire-and-forget broadcast; nobody listening is the normal case. */
function broadcast(entry) {
  chrome.runtime.sendMessage({ type: MSG.LOG_APPENDED, entry }).catch(() => {});
}

export async function log(level, message, data) {
  const entries = await load();
  const settings = await getSettings();
  maxEntries = settings.debug.maxLogEntries ?? maxEntries;
  if (level === LOG_LEVEL.DEBUG && !settings.debug.verbose) return null;

  const entry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    at: new Date().toISOString(),
    level,
    message,
    ...(data === undefined ? {} : { data }),
  };

  entries.push(entry);
  if (entries.length > maxEntries) entries.splice(0, entries.length - maxEntries);

  // Also surface in the service worker console, which is where you land when
  // debugging from chrome://extensions.
  const sink = { debug: console.debug, info: console.info, warn: console.warn, error: console.error }[level];
  sink?.(`[sync] ${message}`, data ?? '');

  scheduleFlush();
  broadcast(entry);
  return entry;
}

export const logger = {
  debug: (message, data) => log(LOG_LEVEL.DEBUG, message, data),
  info: (message, data) => log(LOG_LEVEL.INFO, message, data),
  warn: (message, data) => log(LOG_LEVEL.WARN, message, data),
  error: (message, data) => log(LOG_LEVEL.ERROR, message, data),
};

export async function getLogs() {
  return [...(await load())];
}

export async function clearLogs() {
  buffer = [];
  await chrome.storage.local.set({ [STORAGE_KEYS.LOGS]: [] });
}
