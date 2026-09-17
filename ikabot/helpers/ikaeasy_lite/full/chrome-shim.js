/*
 * IkaEasy full-mode: chrome.* shim
 * --------------------------------
 * Provides just enough of the Chrome extension APIs, backed by page-world
 * equivalents, so the UNMODIFIED IkaEasy extension can run as ordinary page
 * scripts served by ikabot. Loaded before any extension code.
 *
 * Only the surface the extension actually uses is implemented:
 *   runtime.getURL / sendMessage / getManifest / onMessage / lastError
 *   storage.local.get/set + storage.onChanged
 *   notifications / alarms / tabs / scripting   (no-ops or light shims —
 *   these live in the background worker, which full mode does not run)
 */
(function () {
    'use strict';

    var BASE = location.origin + '/ikaeasy-full/';
    var g = (window.chrome = window.chrome || {});

    // ---- runtime --------------------------------------------------------
    g.runtime = g.runtime || {};
    g.runtime.lastError = undefined;

    g.runtime.getURL = function (path) {
        return BASE + String(path || '').replace(/^\/+/, '');
    };

    // getManifest must be synchronous; full-mode.js preloads the manifest and
    // stashes it here before any extension code runs.
    window.__IKEL_MANIFEST__ = window.__IKEL_MANIFEST__ || null;
    g.runtime.getManifest = function () {
        return window.__IKEL_MANIFEST__ || { version: '0.0.0' };
    };

    // The extension registers a background onMessage handler; in full mode
    // there is no background page, so sendMessage handles the known commands
    // directly and registered listeners are simply retained (unused).
    var _listeners = [];
    g.runtime.onMessage = { addListener: function (fn) { _listeners.push(fn); } };

    g.runtime.sendMessage = function (message, callback) {
        var cb = typeof callback === 'function' ? callback : function () {};
        try {
            switch (message && message.cmd) {
                case 'get-version':
                    cb(g.runtime.getManifest().version || null);
                    return;
                case 'load-libs':
                    // full-mode.js injects the libraries itself.
                    cb(true);
                    return;
                case 'ajax':
                    _ikalogsAjax(message).then(cb).catch(function () { cb(null); });
                    return;
                case 'ajax_html':
                    fetch(message.url, { credentials: 'include' })
                        .then(function (r) { return r.text(); })
                        .then(cb).catch(function () { cb(null); });
                    return;
                case 'notification':
                    _notify(message);
                    cb(true);
                    return;
                default:
                    cb(null);
            }
        } catch (e) {
            cb(null);
        }
    };

    // ikalogs.ru lives on another origin. The extension proxied these through
    // its background worker to dodge CORS; in page mode we attempt them
    // directly and degrade gracefully (ikalogs features simply go quiet if the
    // request is blocked).
    function _ikalogsAjax(message) {
        var url = 'https://ikalogs.ru/' + message.url;
        var opts = { credentials: 'include' };
        if (message.method && message.method.toLowerCase() === 'post') {
            opts.method = 'POST';
            opts.headers = { 'Content-type': 'application/x-www-form-urlencoded; charset=UTF-8' };
            opts.body = _toQuery(message.body || {});
        }
        return fetch(url, opts).then(function (r) { return r.json(); });
    }
    function _toQuery(params, prefix) {
        return Object.keys(params).map(function (k) {
            var key = prefix ? prefix + '[' + k + ']' : k, val = params[k];
            if (val && typeof val === 'object') return _toQuery(val, key);
            return encodeURIComponent(key) + '=' + encodeURIComponent(val == null ? '' : val);
        }).join('&');
    }

    function _notify(message) {
        try {
            if (window.Notification && Notification.permission === 'granted') {
                new Notification(message.title || 'IkaEasy', { body: message.body || '' });
            }
        } catch (e) {}
    }

    // ---- storage.local (backed by localStorage) -------------------------
    var STORE_PREFIX = 'ikel_full_';
    var _storeListeners = [];

    function _readAll() {
        var out = {};
        try {
            for (var i = 0; i < localStorage.length; i++) {
                var k = localStorage.key(i);
                if (k && k.indexOf(STORE_PREFIX) === 0) {
                    try { out[k.slice(STORE_PREFIX.length)] = JSON.parse(localStorage.getItem(k)); }
                    catch (e) { out[k.slice(STORE_PREFIX.length)] = localStorage.getItem(k); }
                }
            }
        } catch (e) {}
        return out;
    }

    g.storage = {
        local: {
            get: function (keys, cb) {
                // Signatures: get(cb) | get(key, cb) | get([keys], cb) | get({defaults}, cb)
                if (typeof keys === 'function') { cb = keys; keys = null; }
                var all = _readAll(), result = {};
                if (keys == null) {
                    result = all;
                } else if (typeof keys === 'string') {
                    if (keys in all) result[keys] = all[keys];
                } else if (Array.isArray(keys)) {
                    keys.forEach(function (k) { if (k in all) result[k] = all[k]; });
                } else if (typeof keys === 'object') {
                    Object.keys(keys).forEach(function (k) { result[k] = (k in all) ? all[k] : keys[k]; });
                }
                if (typeof cb === 'function') cb(result);
            },
            set: function (obj, cb) {
                var changes = {};
                try {
                    Object.keys(obj || {}).forEach(function (k) {
                        var oldRaw = localStorage.getItem(STORE_PREFIX + k);
                        var oldVal; try { oldVal = JSON.parse(oldRaw); } catch (e) { oldVal = oldRaw; }
                        localStorage.setItem(STORE_PREFIX + k, JSON.stringify(obj[k]));
                        changes[k] = { oldValue: oldVal, newValue: obj[k] };
                    });
                } catch (e) {}
                _storeListeners.forEach(function (fn) { try { fn(changes, 'local'); } catch (e) {} });
                if (typeof cb === 'function') cb();
            },
            remove: function (key, cb) {
                try {
                    (Array.isArray(key) ? key : [key]).forEach(function (k) {
                        localStorage.removeItem(STORE_PREFIX + k);
                    });
                } catch (e) {}
                if (typeof cb === 'function') cb();
            }
        },
        onChanged: { addListener: function (fn) { _storeListeners.push(fn); } }
    };

    // ---- background-only APIs: light shims / no-ops ---------------------
    g.alarms = {
        create: function () {}, clear: function () {},
        onAlarm: { addListener: function () {} }
    };
    g.notifications = {
        create: function (id, opts) { _notify(opts || {}); },
        clear: function () {},
        onClicked: { addListener: function () {} }
    };
    g.tabs = {
        query: function (q, cb) { if (typeof cb === 'function') cb([]); },
        update: function () {},
        create: function (opts) { try { if (opts && opts.url) window.open(opts.url, '_blank'); } catch (e) {} }
    };
    g.scripting = { executeScript: function () { return Promise.resolve([]); } };
})();
