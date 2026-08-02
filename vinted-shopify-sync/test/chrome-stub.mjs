/**
 * Minimal in-memory stand-in for the Chrome extension APIs the extension uses,
 * so storage, logging and the service worker's message handlers can be exercised
 * under `node --test`. Install it before importing extension modules.
 */

export function installChromeStub({ manifestVersion = '0.1.0' } = {}) {
  const store = new Map();
  const listeners = { message: [], installed: [], startup: [], alarm: [], storage: [] };
  const alarms = new Map();
  const sent = [];

  const chrome = {
    runtime: {
      getManifest: () => ({ version: manifestVersion }),
      openOptionsPage: () => {},
      onInstalled: { addListener: (fn) => listeners.installed.push(fn) },
      onStartup: { addListener: (fn) => listeners.startup.push(fn) },
      onMessage: { addListener: (fn) => listeners.message.push(fn) },
      sendMessage: async (message) => { sent.push(message); },
    },
    storage: {
      local: {
        async get(key) {
          const keys = key === undefined ? [...store.keys()] : Array.isArray(key) ? key : [key];
          return Object.fromEntries(keys.filter((k) => store.has(k)).map((k) => [k, store.get(k)]));
        },
        async set(entries) {
          for (const [k, v] of Object.entries(entries)) store.set(k, v);
          for (const fn of listeners.storage) {
            await fn(Object.fromEntries(Object.keys(entries).map((k) => [k, { newValue: entries[k] }])), 'local');
          }
        },
      },
      onChanged: { addListener: (fn) => listeners.storage.push(fn) },
    },
    alarms: {
      async clear(name) { alarms.delete(name); },
      create(name, options) { alarms.set(name, options); },
      onAlarm: { addListener: (fn) => listeners.alarm.push(fn) },
    },
    tabs: {
      async query() { return []; },
      async create() { return { id: 1 }; },
      async sendMessage() { return {}; },
      onUpdated: { addListener: () => {}, removeListener: () => {} },
    },
  };

  globalThis.chrome = chrome;

  return {
    chrome,
    store,
    alarms,
    sent,
    /** Invokes the service worker's onMessage handler and resolves its reply. */
    dispatch(message) {
      return new Promise((resolve, reject) => {
        const handled = listeners.message.some((fn) => fn(message, {}, resolve));
        if (!handled) reject(new Error(`No handler for ${message.type}`));
      });
    },
  };
}
