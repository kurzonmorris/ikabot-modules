/* IkaEasy-lite feature: all-cities resource overview.
 * Adds a "Resources" button to the control bar. It opens a table of every
 * city and its five resources, read live through the ikabot bridge. */
(function () {
    'use strict';
    if (!window.IKEL) return;
    var IKEL = window.IKEL;

    var RES = [
        { key: 'wood', label: 'Wood' },
        { key: 'wine', label: 'Wine' },
        { key: 'marble', label: 'Marble' },
        { key: 'crystal', label: 'Crystal' },
        { key: 'sulphur', label: 'Sulphur' }
    ];

    function fmt(n) { return (parseInt(n, 10) || 0).toLocaleString(); }

    IKEL.register({
        id: 'resources',
        matches: function () { return true; },   // available in every view
        mount: function () { addButton(); }
    });

    function addButton() {
        if (document.getElementById('ikel-res-btn')) return;
        var bar = document.getElementById('ikel-bar');
        if (!bar) return;
        var btn = IKEL.el('button', { id: 'ikel-res-btn', type: 'button' });
        btn.textContent = 'Resources';
        btn.addEventListener('click', openOverlay);
        // Sit above the toggle button.
        bar.insertBefore(btn, bar.firstChild);
    }

    function openOverlay() {
        var existing = document.getElementById('ikel-res-overlay');
        if (existing) { existing.remove(); return; }   // toggle

        var overlay = IKEL.el('div', { id: 'ikel-res-overlay' });
        overlay.innerHTML =
            '<div class="ikel-res-box">' +
              '<div class="ikel-res-head"><b>All cities — resources</b>' +
                '<a href="#" class="ikel-res-close" title="Close">✕</a></div>' +
              '<div class="ikel-res-content"><div class="ikel-note">Loading…</div></div>' +
            '</div>';
        document.body.appendChild(overlay);

        overlay.addEventListener('click', function (e) {
            if (e.target === overlay || e.target.className === 'ikel-res-close') {
                e.preventDefault();
                overlay.remove();
            }
        });

        IKEL.api('&ikaeasy=resources').then(function (r) { return r.json(); }).then(function (d) {
            if (!d || !d.ok) {
                overlay.querySelector('.ikel-res-content').innerHTML =
                    '<div class="ikel-note">' + ((d && d.error) || 'Could not read resources.') + '</div>';
                return;
            }
            renderTable(overlay.querySelector('.ikel-res-content'), d.cities || []);
        }).catch(function () {
            overlay.querySelector('.ikel-res-content').innerHTML =
                '<div class="ikel-note">Play through the ikabot web server to use this.</div>';
        });
    }

    function renderTable(host, cities) {
        if (!cities.length) {
            host.innerHTML = '<div class="ikel-note">No cities found.</div>';
            return;
        }
        var totals = { wood: 0, wine: 0, marble: 0, crystal: 0, sulphur: 0 };
        var rows = cities.map(function (c) {
            var cells = RES.map(function (res) {
                totals[res.key] += parseInt(c[res.key], 10) || 0;
                return '<td class="ikel-num">' + fmt(c[res.key]) + '</td>';
            }).join('');
            return '<tr><td>' + esc(c.name) + '</td>' + cells + '</tr>';
        }).join('');

        var totalCells = RES.map(function (res) {
            return '<td class="ikel-num">' + fmt(totals[res.key]) + '</td>';
        }).join('');

        host.innerHTML =
            '<table class="ikel-table"><thead><tr><th>City</th>' +
              RES.map(function (res) { return '<th>' + res.label + '</th>'; }).join('') +
            '</tr></thead><tbody>' + rows +
            '<tr class="ikel-group"><td><b>Total</b></td>' + totalCells + '</tr>' +
            '</tbody></table>';
    }

    function esc(s) { return String(s).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }
})();
