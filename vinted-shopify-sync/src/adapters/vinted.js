/**
 * Vinted adapter.
 *
 * Vinted publishes no partner API, so everything goes through a tab that is
 * already signed in: the service worker asks the content script to read the
 * wardrobe using the site's own session. Nothing is scraped anonymously and no
 * credentials are handled by the extension.
 *
 * Phase 1 implements reads. Writes are deliberately unimplemented — editing a
 * listing means driving Vinted's own edit form, and that has to be built against
 * the live markup rather than guessed at.
 */

import { MSG } from '../lib/constants.js';
import { logger } from '../lib/logger.js';

export class VintedAdapter {
  constructor(settings) {
    this.settings = settings.vinted;
  }

  get configured() {
    return Boolean(this.settings.domain);
  }

  get origin() {
    return `https://${this.settings.domain.replace(/^https?:\/\//, '').replace(/\/$/, '')}`;
  }

  /** Finds a signed-in Vinted tab, opening a background one if there is none. */
  async #tab() {
    const host = new URL(this.origin).hostname;
    const [existing] = await chrome.tabs.query({ url: `https://${host}/*` });
    if (existing) return existing;

    logger.info(`Opening a Vinted tab (${host}) — it must be signed in to read the wardrobe.`);
    const tab = await chrome.tabs.create({ url: `${this.origin}/member/general/settings`, active: false });
    await waitForTabLoad(tab.id);
    return tab;
  }

  async #ask(type, payload = {}, { timeoutMs = 45000 } = {}) {
    const tab = await this.#tab();
    const response = await withTimeout(
      chrome.tabs.sendMessage(tab.id, { type, ...payload }),
      timeoutMs,
      `Vinted content script did not answer "${type}" in ${timeoutMs / 1000}s`,
    ).catch((error) => {
      if (String(error).includes('Receiving end does not exist')) {
        throw new Error('No Vinted content script in that tab — reload the Vinted tab and retry.');
      }
      throw error;
    });

    if (!response) throw new Error(`Empty response from Vinted content script for "${type}"`);
    if (response.error) throw new Error(response.error);
    return response;
  }

  async testConnection() {
    const probe = await this.#ask(MSG.VINTED_PROBE, {}, { timeoutMs: 15000 });
    if (!probe.signedIn) {
      throw new Error(`Not signed in on ${this.settings.domain}. Log in to Vinted in that tab, then retry.`);
    }
    return { ok: true, ...probe };
  }

  async fetchItems() {
    const { items = [] } = await this.#ask(MSG.VINTED_SCRAPE_WARDROBE, {
      username: this.settings.username,
    });
    logger.info(`Vinted: loaded ${items.length} listings`);
    return items;
  }

  // --- writes (phase 2) ------------------------------------------------------

  async setPrice() {
    throw new Error('Writing prices to Vinted is not implemented yet (phase 2).');
  }

  async setQuantity() {
    throw new Error('Writing stock to Vinted is not implemented yet (phase 2).');
  }

  async updateContent() {
    throw new Error('Writing listing content to Vinted is not implemented yet (phase 2).');
  }

  async archive() {
    throw new Error('Hiding/deleting Vinted listings is not implemented yet (phase 2).');
  }
}

function withTimeout(promise, ms, message) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(message)), ms)),
  ]);
}

function waitForTabLoad(tabId, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error('Timed out waiting for the Vinted tab to load'));
    }, timeoutMs);

    function listener(id, info) {
      if (id !== tabId || info.status !== 'complete') return;
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      // The SPA still needs a beat after `complete` before its data is queryable.
      setTimeout(resolve, 1500);
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}
