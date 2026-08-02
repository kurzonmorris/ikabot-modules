/**
 * Pairs the two catalogues and works out what would have to change for them to
 * agree. Pure functions only — no network, no storage — so the plan can be shown
 * to the user (dry run) before anything is written.
 */

import { DIRECTION } from './constants.js';
import { keyFor, toMinorUnits, normaliseSize, normaliseText } from './item.js';

export const ACTION = {
  UPDATE_PRICE: 'update-price',
  UPDATE_INVENTORY: 'update-inventory',
  UPDATE_CONTENT: 'update-content',
  CREATE: 'create',
  ARCHIVE: 'archive',
  UNMATCHED: 'unmatched',
};

const CONTENT_FIELDS = ['title', 'description', 'brand', 'size', 'colour', 'condition'];

function index(items) {
  const map = new Map();
  for (const item of items) {
    const key = keyFor(item);
    if (!key) continue;
    // Duplicate keys are a data problem on the source side; keep the first and
    // let the caller see the rest as unmatched.
    if (!map.has(key)) map.set(key, item);
  }
  return map;
}

function targetOf(direction) {
  if (direction === DIRECTION.SHOPIFY_TO_VINTED) return 'vinted';
  if (direction === DIRECTION.VINTED_TO_SHOPIFY) return 'shopify';
  return null;
}

function sourceOf(direction) {
  const target = targetOf(direction);
  return target === 'vinted' ? 'shopify' : target === 'shopify' ? 'vinted' : null;
}

function pick(pair, side) {
  return side === 'vinted' ? pair.vinted : pair.shopify;
}

/** An item counts as sold when the platform says so, or when stock hits zero. */
export function isSold(item) {
  return item.status === 'sold' || (item.status !== 'draft' && item.quantity <= 0);
}

function contentDiff(from, to) {
  const changes = [];
  for (const field of CONTENT_FIELDS) {
    const a = field === 'size' ? normaliseSize(from[field]) : normaliseText(from[field]).toLowerCase();
    const b = field === 'size' ? normaliseSize(to[field]) : normaliseText(to[field]).toLowerCase();
    if (a !== b) changes.push({ field, from: to[field], to: from[field] });
  }
  return changes;
}

/**
 * @returns {{actions: Array, pairs: Array, stats: object}}
 */
export function buildPlan({ vintedItems = [], shopifyItems = [], settings }) {
  const rules = settings.sync;
  const vintedIndex = index(vintedItems);
  const shopifyIndex = index(shopifyItems);
  const keys = new Set([...vintedIndex.keys(), ...shopifyIndex.keys()]);

  const actions = [];
  const pairs = [];
  const stats = { matched: 0, vintedOnly: 0, shopifyOnly: 0 };

  for (const key of keys) {
    const vinted = vintedIndex.get(key) ?? null;
    const shopify = shopifyIndex.get(key) ?? null;
    const pair = { key, vinted, shopify };
    pairs.push(pair);

    if (!vinted || !shopify) {
      const present = vinted ? 'vinted' : 'shopify';
      const missing = vinted ? 'shopify' : 'vinted';
      if (vinted) stats.vintedOnly += 1; else stats.shopifyOnly += 1;

      actions.push({
        type: rules.createMissing ? ACTION.CREATE : ACTION.UNMATCHED,
        key,
        target: missing,
        source: present,
        item: vinted ?? shopify,
        reason: `Present on ${present} only`,
      });
      continue;
    }

    stats.matched += 1;

    // --- sold / stock parity -------------------------------------------------
    const inventoryTarget = targetOf(rules.inventory);
    if (inventoryTarget) {
      const from = pick(pair, sourceOf(rules.inventory));
      const to = pick(pair, inventoryTarget);
      if (rules.archiveSold && isSold(from) && !isSold(to)) {
        actions.push({
          type: ACTION.ARCHIVE,
          key,
          target: inventoryTarget,
          item: to,
          reason: `Sold on ${from.source}`,
        });
      } else if (from.quantity !== to.quantity) {
        actions.push({
          type: ACTION.UPDATE_INVENTORY,
          key,
          target: inventoryTarget,
          item: to,
          changes: [{ field: 'quantity', from: to.quantity, to: from.quantity }],
          reason: 'Stock differs',
        });
      }
    }

    // --- price ---------------------------------------------------------------
    const priceTarget = targetOf(rules.price);
    if (priceTarget) {
      const from = pick(pair, sourceOf(rules.price));
      const to = pick(pair, priceTarget);
      const a = toMinorUnits(from.price);
      const b = toMinorUnits(to.price);
      if (a !== null && a !== b) {
        actions.push({
          type: ACTION.UPDATE_PRICE,
          key,
          target: priceTarget,
          item: to,
          changes: [{ field: 'price', from: to.price, to: from.price }],
          reason: 'Price differs',
        });
      }
    }

    // --- title / description / garment attributes ----------------------------
    const contentTarget = targetOf(rules.content);
    if (contentTarget) {
      const from = pick(pair, sourceOf(rules.content));
      const to = pick(pair, contentTarget);
      const changes = contentDiff(from, to);
      if (changes.length) {
        actions.push({
          type: ACTION.UPDATE_CONTENT,
          key,
          target: contentTarget,
          item: to,
          changes,
          reason: `${changes.length} field(s) differ`,
        });
      }
    }
  }

  return { actions, pairs, stats };
}

export function summarise(plan) {
  const counts = {};
  for (const action of plan.actions) counts[action.type] = (counts[action.type] ?? 0) + 1;
  return { ...plan.stats, actions: plan.actions.length, byType: counts };
}
