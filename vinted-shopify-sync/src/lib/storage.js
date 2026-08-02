/**
 * Thin wrapper over chrome.storage.local.
 *
 * Everything lives in `local` rather than `sync`: the Shopify Admin token must
 * never leave the machine, and the link table can outgrow the sync quota.
 */

import { STORAGE_KEYS, DEFAULT_SETTINGS } from './constants.js';

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Recursive merge so new default keys appear for users upgrading the extension. */
export function mergeDefaults(defaults, stored) {
  if (!isPlainObject(stored)) return structuredClone(defaults);
  const out = structuredClone(defaults);
  for (const [key, value] of Object.entries(stored)) {
    out[key] = isPlainObject(value) && isPlainObject(out[key])
      ? mergeDefaults(out[key], value)
      : value;
  }
  return out;
}

export async function getSettings() {
  const raw = await chrome.storage.local.get(STORAGE_KEYS.SETTINGS);
  return mergeDefaults(DEFAULT_SETTINGS, raw[STORAGE_KEYS.SETTINGS]);
}

export async function saveSettings(partial) {
  const next = mergeDefaults(await getSettings(), partial);
  await chrome.storage.local.set({ [STORAGE_KEYS.SETTINGS]: next });
  return next;
}

/**
 * The link table is the heart of parity: one row per SKU, holding the ids each
 * platform knows the garment by plus the last synced content hash.
 */
export async function getLinks() {
  const raw = await chrome.storage.local.get(STORAGE_KEYS.LINKS);
  return raw[STORAGE_KEYS.LINKS] ?? {};
}

export async function upsertLink(sku, patch) {
  const links = await getLinks();
  links[sku] = { ...(links[sku] ?? {}), ...patch, sku, updatedAt: Date.now() };
  await chrome.storage.local.set({ [STORAGE_KEYS.LINKS]: links });
  return links[sku];
}

export async function replaceLinks(links) {
  await chrome.storage.local.set({ [STORAGE_KEYS.LINKS]: links });
}

export async function getLastRun() {
  const raw = await chrome.storage.local.get(STORAGE_KEYS.LAST_RUN);
  return raw[STORAGE_KEYS.LAST_RUN] ?? null;
}

export async function setLastRun(summary) {
  await chrome.storage.local.set({ [STORAGE_KEYS.LAST_RUN]: summary });
}

export async function getSnapshot() {
  const raw = await chrome.storage.local.get(STORAGE_KEYS.SNAPSHOT);
  return raw[STORAGE_KEYS.SNAPSHOT] ?? { vinted: [], shopify: [], takenAt: null };
}

export async function setSnapshot(snapshot) {
  await chrome.storage.local.set({ [STORAGE_KEYS.SNAPSHOT]: snapshot });
}
