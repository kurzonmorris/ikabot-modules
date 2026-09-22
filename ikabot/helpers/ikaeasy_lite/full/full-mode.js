/*
 * IkaEasy full-mode entry
 * -----------------------
 * Boots the UNMODIFIED IkaEasy extension in the page, served by ikabot, with
 * no Chrome extension installed. Loaded by loader.js only when full mode is
 * explicitly enabled (opt-in). The chrome-shim.js file is loaded immediately
 * before this one, so window.chrome.* is already in place.
 *
 * Sequence: preload manifest (for getManifest) -> load jQuery/lodash/moment ->
 * dynamic-import the extension's own initModule.js, which chains the rest of
 * the extension exactly as it does when installed.
 */
(function () {
    'use strict';
    if (window.__IKEL_FULL_BOOTED__) { return; }
    window.__IKEL_FULL_BOOTED__ = true;

    var BASE = location.origin + '/ikaeasy-full/';

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = src;
            s.onload = resolve;
            s.onerror = function () { reject(new Error('failed to load ' + src)); };
            (document.head || document.documentElement).appendChild(s);
        });
    }

    // Load a library only if the page does not already provide it. The game
    // page ships its own jQuery; loading a second copy would clobber the
    // game's $ and can break it. lodash and moment are normally absent, so we
    // load those. This avoids the library clash that blocks full mode.
    function ensureLib(globalName, src) {
        if (window[globalName]) return Promise.resolve();
        return loadScript(src);
    }

    function fail(e) { try { console.error('[IkaEasy full] boot failed:', e); } catch (_) {} }

    // 1. Preload the manifest so chrome.runtime.getManifest() is synchronous.
    fetch(BASE + 'manifest.json', { credentials: 'include' })
        .then(function (r) { return r.json(); })
        .then(function (m) { window.__IKEL_MANIFEST__ = m; }, function () { window.__IKEL_MANIFEST__ = { version: '0.0.0' }; })
        // 2. Make sure $, _ and moment exist, reusing the game's where present.
        .then(function () { return ensureLib('jQuery', BASE + 'js/libs/jquery.js'); })
        .then(function () { if (window.jQuery && !window.$) window.$ = window.jQuery; })
        .then(function () { return ensureLib('_', BASE + 'js/libs/lodash.js'); })
        .then(function () { return ensureLib('moment', BASE + 'js/libs/moment-with-locales.min.js'); })
        // 3. Boot the extension. initModule.js uses relative imports, which
        //    resolve against BASE and are served by ikabot.
        .then(function () { return import(BASE + 'js/initModule.js'); })
        .catch(fail);
})();
