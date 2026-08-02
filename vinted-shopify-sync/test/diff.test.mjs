/**
 * Tests for the pairing/diff logic — the part that decides what "in parity"
 * means. Runs outside Chrome because diff.js and item.js touch no extension API.
 *
 *   node --test vinted-shopify-sync/test/
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { buildPlan, summarise, ACTION, isSold } from '../src/lib/diff.js';
import { makeItem, keyFor, normaliseSize, toMinorUnits } from '../src/lib/item.js';
import { DEFAULT_SETTINGS, DIRECTION } from '../src/lib/constants.js';
import { mergeDefaults } from '../src/lib/storage.js';

const settingsWith = (sync) => mergeDefaults(DEFAULT_SETTINGS, { sync });

const vintedItem = (fields) => makeItem({ source: 'vinted', sourceId: 'v1', quantity: 1, status: 'active', ...fields });
const shopifyItem = (fields) => makeItem({ source: 'shopify', sourceId: 'gid://p/1', variantId: 'gid://v/1', quantity: 1, status: 'active', ...fields });

test('sizes normalise across platform spellings', () => {
  assert.equal(normaliseSize('UK 10'), normaliseSize('10'));
  assert.equal(normaliseSize('Size M'), normaliseSize('m'));
  assert.notEqual(normaliseSize('10'), normaliseSize('12'));
});

test('prices compare in minor units', () => {
  assert.equal(toMinorUnits('12.30'), 1230);
  assert.equal(toMinorUnits(0.1 + 0.2), 30);
  assert.equal(toMinorUnits(''), null);
});

test('SKU wins over the title fallback as the pairing key', () => {
  assert.equal(keyFor(makeItem({ sku: 'DRS-001', title: 'Floral dress' })), 'drs-001');
  assert.equal(keyFor(makeItem({ title: 'Floral dress', size: 'UK 10' })), 'title:floral dress:10');
  assert.equal(keyFor(makeItem({})), '');
});

test('identical catalogues produce no actions', () => {
  const plan = buildPlan({
    vintedItems: [vintedItem({ sku: 'DRS-001', title: 'Floral dress', price: 24, size: 'UK 10' })],
    shopifyItems: [shopifyItem({ sku: 'DRS-001', title: 'Floral dress', price: 24, size: '10' })],
    settings: settingsWith({ content: DIRECTION.OFF }),
  });
  assert.deepEqual(plan.actions, []);
  assert.equal(summarise(plan).matched, 1);
});

test('a price difference is pushed in the configured direction only', () => {
  const plan = buildPlan({
    vintedItems: [vintedItem({ sku: 'DRS-001', price: 20 })],
    shopifyItems: [shopifyItem({ sku: 'DRS-001', price: 24 })],
    settings: settingsWith({ price: DIRECTION.SHOPIFY_TO_VINTED, content: DIRECTION.OFF }),
  });

  const [action] = plan.actions.filter((a) => a.type === ACTION.UPDATE_PRICE);
  assert.equal(action.target, 'vinted');
  assert.deepEqual(action.changes, [{ field: 'price', from: 20, to: 24 }]);
});

test('an item sold on Vinted archives the Shopify product', () => {
  const plan = buildPlan({
    vintedItems: [vintedItem({ sku: 'DRS-001', status: 'sold', quantity: 0 })],
    shopifyItems: [shopifyItem({ sku: 'DRS-001', status: 'active', quantity: 1 })],
    settings: settingsWith({ inventory: DIRECTION.VINTED_TO_SHOPIFY, price: DIRECTION.OFF, content: DIRECTION.OFF }),
  });

  assert.equal(plan.actions.length, 1);
  assert.equal(plan.actions[0].type, ACTION.ARCHIVE);
  assert.equal(plan.actions[0].target, 'shopify');
});

test('zero stock counts as sold, a draft does not', () => {
  assert.equal(isSold(makeItem({ quantity: 0, status: 'active' })), true);
  assert.equal(isSold(makeItem({ quantity: 0, status: 'draft' })), false);
  assert.equal(isSold(makeItem({ quantity: 3, status: 'active' })), false);
});

test('one-sided items are reported, not created, unless createMissing is on', () => {
  const input = {
    vintedItems: [vintedItem({ sku: 'ONLY-V', title: 'Denim jacket' })],
    shopifyItems: [],
  };

  const reported = buildPlan({ ...input, settings: settingsWith({}) });
  assert.equal(reported.actions[0].type, ACTION.UNMATCHED);
  assert.equal(reported.actions[0].target, 'shopify');
  assert.equal(summarise(reported).vintedOnly, 1);

  const creating = buildPlan({ ...input, settings: settingsWith({ createMissing: true }) });
  assert.equal(creating.actions[0].type, ACTION.CREATE);
});

test('garment attribute drift is caught as a content change', () => {
  const plan = buildPlan({
    vintedItems: [vintedItem({ sku: 'DRS-001', title: 'Floral dress', brand: 'Zara', size: 'UK 10' })],
    shopifyItems: [shopifyItem({ sku: 'DRS-001', title: 'Floral dress', brand: 'Zaraa', size: 'UK 12' })],
    settings: settingsWith({ content: DIRECTION.VINTED_TO_SHOPIFY, price: DIRECTION.OFF, inventory: DIRECTION.OFF }),
  });

  const [action] = plan.actions;
  assert.equal(action.type, ACTION.UPDATE_CONTENT);
  assert.equal(action.target, 'shopify');
  assert.deepEqual(
    action.changes.map((c) => c.field).sort(),
    ['brand', 'size'],
  );
});

test('items with no SKU and no title are left out of pairing entirely', () => {
  const plan = buildPlan({
    vintedItems: [vintedItem({ title: '' })],
    shopifyItems: [],
    settings: settingsWith({}),
  });
  assert.deepEqual(plan.actions, []);
});
