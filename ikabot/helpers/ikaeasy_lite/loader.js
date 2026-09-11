/*
 * IkaEasy-lite loader
 * -------------------
 * Served by the ikabot web server and injected into the game page, so the
 * IkaEasy feature panels work in ANY browser with no Chrome extension.
 *
 * This runs in the PAGE world (a normal <script src>), so unlike the
 * extension's isolated-world content scripts it can read the game's own
 * globals directly: `ikariam.templateView.id` (which building view is open),
 * `ikariam.model.cityId` (current city), and the page's jQuery for its
 * ajaxSuccess hook.
 *
 * It is deliberately self-contained: no imports, no chrome.* APIs, no build
 * step. Features register themselves on window.IKEL.features.
 */
(function () {
    'use strict';

    // ---- Single-instance + extension deferral ---------------------------
    // If our own loader already ran, stop.
    if (window.__IKEL_LOADED__) { return; }
    window.__IKEL_LOADED__ = true;

    var DISABLE_KEY = 'ikaeasy_lite_off';

    // The IkaEasy extension marks the document when it boots. If it is present
    // we defer to it entirely (existing extension users are unaffected) — but
    // we still show the toggle so the user understands why lite is dormant.
    function extensionPresent() {
        try {
            return document.documentElement.getAttribute('data-ikaeasy-ext') === '1' ||
                   !!window.__IKAEASY_EXT__ ||
                   !!document.getElementById('sandbox'); // extension's sandbox iframe
        } catch (e) { return false; }
    }

    // ---- Tiny helpers ---------------------------------------------------
    var IKEL = window.IKEL = {
        version: null,
        base: '/ikaeasy-lite/',
        features: [],          // each: { id, matches(view), mount(ctx) }
        register: function (f) { this.features.push(f); },
        el: function (tag, attrs, html) {
            var e = document.createElement(tag);
            if (attrs) { for (var k in attrs) { if (k === 'class') e.className = attrs[k]; else e.setAttribute(k, attrs[k]); } }
            if (html != null) e.innerHTML = html;
            return e;
        },
        // Same-origin bridge call — follows whatever host serves the game.
        api: function (query) {
            return fetch(location.origin + '/index.php?ikabot=1&action=ikaeasy' + (query || ''),
                         { credentials: 'include' });
        },
        post: function (body) {
            return fetch(location.origin + '/index.php?ikabot=1&action=ikaeasy', {
                method: 'POST', credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
        },
        currentView: function () {
            try { return (window.ikariam && ikariam.templateView && ikariam.templateView.id) || null; }
            catch (e) { return null; }
        },
        currentCityId: function () {
            try {
                if (window.ikariam && ikariam.model && ikariam.model.cityId != null) return String(ikariam.model.cityId);
            } catch (e) {}
            return null;
        }
    };

    // ---- On/off toggle (escape hatch) -----------------------------------
    function isDisabled() {
        try { return localStorage.getItem(DISABLE_KEY) === '1'; } catch (e) { return false; }
    }
    function setDisabled(v) {
        try { localStorage.setItem(DISABLE_KEY, v ? '1' : '0'); } catch (e) {}
    }

    function renderToggle() {
        var existing = document.getElementById('ikel-toggle');
        if (existing) existing.remove();
        var btn = IKEL.el('button', { id: 'ikel-toggle', type: 'button' });
        var deferred = extensionPresent();
        function label() {
            if (deferred) return 'IkaEasy: extension active';
            return isDisabled() ? 'IkaEasy: OFF' : 'IkaEasy: on';
        }
        btn.textContent = label();
        if (deferred || isDisabled()) btn.classList.add('ikel-off');
        btn.addEventListener('click', function () {
            if (deferred) { return; } // nothing to toggle; extension owns the page
            setDisabled(!isDisabled());
            btn.textContent = label();
            btn.classList.toggle('ikel-off', isDisabled());
            if (isDisabled()) { removeAllPanels(); }
            else { mountForView(); }
        });
        document.body.appendChild(btn);
    }

    function removeAllPanels() {
        var nodes = document.querySelectorAll('.ikel-panel');
        for (var i = 0; i < nodes.length; i++) nodes[i].remove();
    }

    // ---- Feature mounting ----------------------------------------------
    var _mounting = false;
    function mountForView() {
        if (_mounting) return;
        if (extensionPresent() || isDisabled()) return;
        var view = IKEL.currentView();
        if (!view) return;
        _mounting = true;
        try {
            for (var i = 0; i < IKEL.features.length; i++) {
                var f = IKEL.features[i];
                try {
                    if (f.matches(view)) {
                        f.mount({ view: view, cityId: IKEL.currentCityId() });
                    }
                } catch (e) { /* one feature failing must not stop the others */ }
            }
        } finally {
            _mounting = false;
        }
    }

    // Re-evaluate on every game AJAX navigation (the game is a single page).
    function hookNavigation() {
        try {
            if (window.jQuery) {
                window.jQuery(document).ajaxComplete(function () {
                    setTimeout(mountForView, 60);
                });
            }
        } catch (e) {}
        // Fallback: poll for view changes in case jQuery isn't reachable.
        var lastView = null;
        setInterval(function () {
            if (extensionPresent() || isDisabled()) return;
            var v = IKEL.currentView();
            if (v && v !== lastView) { lastView = v; mountForView(); }
        }, 1500);
    }

    // ---- Boot -----------------------------------------------------------
    function boot() {
        // Read our version off the loader's own <script src=?v=...>.
        try {
            var s = document.querySelector('script[data-ikaeasy-lite]');
            if (s) { var m = /[?&]v=([^&]+)/.exec(s.getAttribute('src') || ''); if (m) IKEL.version = m[1]; }
        } catch (e) {}

        renderToggle();

        if (extensionPresent()) { return; } // extension wins; stay dormant

        // Load the shared stylesheet, then the feature modules, then mount.
        var link = IKEL.el('link', { rel: 'stylesheet', href: IKEL.base + 'css/lite.css?v=' + (IKEL.version || '') });
        document.head.appendChild(link);

        var features = ['production', 'tavern', 'construction', 'transport'];
        var pending = features.length;
        features.forEach(function (name) {
            var sc = IKEL.el('script', { src: IKEL.base + 'features/' + name + '.js?v=' + (IKEL.version || '') });
            sc.onload = sc.onerror = function () { if (--pending === 0) { hookNavigation(); mountForView(); } };
            document.head.appendChild(sc);
        });
    }

    // The extension may boot slightly after us; give it a moment to mark the
    // document before we decide, so it deterministically wins when installed.
    function start() {
        setTimeout(boot, 400);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
