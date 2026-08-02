/**
 * Orchestrates a run: read both catalogues, diff them, then (unless this is a
 * dry run) apply the plan one action at a time.
 *
 * Every action is logged with its before/after values, so a run can be replayed
 * or audited afterwards — including by an external monitor reading the exported
 * report.
 */

import { VintedAdapter } from '../adapters/vinted.js';
import { ShopifyAdapter } from '../adapters/shopify.js';
import { buildPlan, summarise, ACTION } from './diff.js';
import { logger } from './logger.js';
import { getSettings, setLastRun, setSnapshot, upsertLink } from './storage.js';
import { keyFor } from './item.js';

export class SyncRun {
  constructor({ onProgress } = {}) {
    this.onProgress = onProgress ?? (() => {});
    this.cancelled = false;
  }

  cancel() {
    this.cancelled = true;
  }

  #check() {
    if (this.cancelled) throw new Error('Cancelled');
  }

  #progress(phase, detail = {}) {
    this.onProgress({ phase, ...detail });
    logger.debug(`phase: ${phase}`, detail);
  }

  async run({ dryRunOverride } = {}) {
    const startedAt = Date.now();
    const settings = await getSettings();
    const dryRun = dryRunOverride ?? settings.sync.dryRun;

    const vinted = new VintedAdapter(settings);
    const shopify = new ShopifyAdapter(settings);

    logger.info(`Run started (${dryRun ? 'dry run — nothing will be written' : 'LIVE — changes will be written'})`);

    this.#progress('fetch:shopify');
    const shopifyItems = await shopify.fetchItems();
    this.#check();

    this.#progress('fetch:vinted');
    const vintedItems = await vinted.fetchItems();
    this.#check();

    this.#progress('diff');
    const plan = buildPlan({ vintedItems, shopifyItems, settings });
    const summary = summarise(plan);
    logger.info('Plan built', summary);

    const results = { applied: 0, skipped: 0, failed: 0, actions: [] };

    for (const [i, action] of plan.actions.entries()) {
      this.#check();
      this.#progress('apply', { index: i + 1, total: plan.actions.length, action: action.type });

      const record = {
        type: action.type,
        key: action.key,
        target: action.target,
        title: action.item?.title ?? '',
        changes: action.changes ?? [],
        reason: action.reason,
        status: 'pending',
      };

      if (dryRun || action.type === ACTION.UNMATCHED) {
        record.status = dryRun ? 'would-apply' : 'skipped';
        results.skipped += 1;
        results.actions.push(record);
        logger.info(`${record.status}: ${action.type} → ${action.target} [${action.key}]`, action.changes ?? action.reason);
        continue;
      }

      try {
        await this.#apply(action, { vinted, shopify, settings });
        record.status = 'applied';
        results.applied += 1;
        logger.info(`applied: ${action.type} → ${action.target} [${action.key}]`, action.changes ?? action.reason);
      } catch (error) {
        record.status = 'failed';
        record.error = String(error?.message ?? error);
        results.failed += 1;
        logger.error(`failed: ${action.type} → ${action.target} [${action.key}]`, record.error);
      }
      results.actions.push(record);
    }

    // Remember the pairing so later runs can match even if a title changes.
    for (const pair of plan.pairs) {
      if (!pair.vinted || !pair.shopify) continue;
      await upsertLink(pair.key, {
        vintedId: pair.vinted.sourceId,
        shopifyProductId: pair.shopify.sourceId,
        shopifyVariantId: pair.shopify.variantId,
      });
    }

    await setSnapshot({ vinted: vintedItems, shopify: shopifyItems, takenAt: new Date().toISOString() });

    const lastRun = {
      startedAt: new Date(startedAt).toISOString(),
      finishedAt: new Date().toISOString(),
      durationMs: Date.now() - startedAt,
      dryRun,
      summary,
      results,
      counts: { vinted: vintedItems.length, shopify: shopifyItems.length },
    };
    await setLastRun(lastRun);

    logger.info(
      `Run finished in ${(lastRun.durationMs / 1000).toFixed(1)}s — ` +
      `${results.applied} applied, ${results.skipped} skipped, ${results.failed} failed`,
    );
    this.#progress('done', { summary });
    return lastRun;
  }

  async #apply(action, { vinted, shopify, settings }) {
    const adapter = action.target === 'vinted' ? vinted : shopify;
    const item = action.item;

    switch (action.type) {
      case ACTION.UPDATE_PRICE:
        return adapter.setPrice(item, action.changes[0].to);
      case ACTION.UPDATE_INVENTORY:
        return adapter.setQuantity(item, action.changes[0].to, settings.shopify.locationId);
      case ACTION.UPDATE_CONTENT:
        return adapter.updateContent(item, action.changes);
      case ACTION.ARCHIVE:
        return adapter.archive(item);
      case ACTION.CREATE:
        throw new Error('Creating listings from scratch is not implemented yet (phase 2).');
      default:
        throw new Error(`Unknown action type: ${action.type}`);
    }
  }
}

/** Read-only preview used by the popup's "Preview differences" button. */
export async function previewPlan() {
  const settings = await getSettings();
  const shopifyItems = await new ShopifyAdapter(settings).fetchItems();
  const vintedItems = await new VintedAdapter(settings).fetchItems();
  const plan = buildPlan({ vintedItems, shopifyItems, settings });
  return {
    summary: summarise(plan),
    actions: plan.actions.map((a) => ({
      type: a.type,
      key: a.key,
      target: a.target,
      title: a.item?.title ?? '',
      changes: a.changes ?? [],
      reason: a.reason,
    })),
    unkeyed: {
      vinted: vintedItems.filter((i) => !keyFor(i)).length,
      shopify: shopifyItems.filter((i) => !keyFor(i)).length,
    },
  };
}
