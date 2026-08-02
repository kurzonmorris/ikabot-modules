import { MSG, DIRECTION } from '../lib/constants.js';
import { getSettings, saveSettings } from '../lib/storage.js';

const $ = (selector) => document.querySelector(selector);

const DIRECTION_LABELS = [
  [DIRECTION.SHOPIFY_TO_VINTED, 'Shopify → Vinted'],
  [DIRECTION.VINTED_TO_SHOPIFY, 'Vinted → Shopify'],
  [DIRECTION.OFF, "don't sync"],
];

function fillDirectionSelects() {
  for (const select of document.querySelectorAll('select[data-rule]')) {
    select.innerHTML = '';
    for (const [value, label] of DIRECTION_LABELS) {
      select.append(new Option(label, value));
    }
  }
}

function setResult(id, message, tone = '') {
  const node = $(id);
  node.textContent = message;
  node.dataset.tone = tone;
}

async function load() {
  const settings = await getSettings();

  $('#shop-domain').value = settings.shopify.shopDomain;
  $('#shop-token').value = settings.shopify.accessToken;
  $('#shop-version').value = settings.shopify.apiVersion;
  if (settings.shopify.locationId) {
    $('#shop-location').append(new Option(settings.shopify.locationId, settings.shopify.locationId, true, true));
  }

  $('#vinted-domain').value = settings.vinted.domain;
  $('#vinted-username').value = settings.vinted.username;

  $('#rule-inventory').value = settings.sync.inventory;
  $('#rule-price').value = settings.sync.price;
  $('#rule-content').value = settings.sync.content;
  $('#archive-sold').checked = settings.sync.archiveSold;
  $('#create-missing').checked = settings.sync.createMissing;

  $('#dry-run').checked = settings.sync.dryRun;
  $('#interval').value = String(settings.sync.intervalMinutes);
  $('#max-logs').value = settings.debug.maxLogEntries;
  $('#verbose').checked = settings.debug.verbose;
}

async function save() {
  await saveSettings({
    shopify: {
      shopDomain: $('#shop-domain').value.trim().replace(/^https?:\/\//, '').replace(/\/$/, ''),
      accessToken: $('#shop-token').value.trim(),
      apiVersion: $('#shop-version').value.trim() || '2025-01',
      locationId: $('#shop-location').value,
    },
    vinted: {
      domain: $('#vinted-domain').value,
      username: $('#vinted-username').value.trim(),
    },
    sync: {
      inventory: $('#rule-inventory').value,
      price: $('#rule-price').value,
      content: $('#rule-content').value,
      archiveSold: $('#archive-sold').checked,
      createMissing: $('#create-missing').checked,
      dryRun: $('#dry-run').checked,
      intervalMinutes: Number($('#interval').value),
    },
    debug: {
      verbose: $('#verbose').checked,
      maxLogEntries: Number($('#max-logs').value) || 500,
    },
  });
  setResult('#save-result', 'Saved.', 'ok');
  setTimeout(() => setResult('#save-result', ''), 2500);
}

$('#save').addEventListener('click', save);

$('#test-shopify').addEventListener('click', async () => {
  await save(); // test what is on screen, not what was last saved
  setResult('#shopify-result', 'connecting…');
  const response = await chrome.runtime.sendMessage({ type: MSG.TEST_SHOPIFY });
  if (!response?.ok) return setResult('#shopify-result', response?.error ?? 'failed', 'error');

  const { shop, currency, locations, suggestedLocationId } = response.result;
  const select = $('#shop-location');
  const chosen = select.value || suggestedLocationId;
  select.innerHTML = '';
  for (const location of locations) {
    select.append(new Option(location.name, location.id, false, location.id === chosen));
  }
  setResult('#shopify-result', `Connected to ${shop} (${currency})`, 'ok');
});

$('#test-vinted').addEventListener('click', async () => {
  await save();
  setResult('#vinted-result', 'checking the Vinted tab…');
  const response = await chrome.runtime.sendMessage({ type: MSG.TEST_VINTED });
  setResult(
    '#vinted-result',
    response?.ok ? `Signed in as ${response.result.username || response.result.userId}` : response?.error ?? 'failed',
    response?.ok ? 'ok' : 'error',
  );
});

fillDirectionSelects();
load();
