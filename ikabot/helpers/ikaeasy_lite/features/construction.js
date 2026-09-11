/* IkaEasy-lite feature: Construction Manager report (town hall view). */
(function () {
    'use strict';
    if (!window.IKEL) return;
    var IKEL = window.IKEL;

    function findAnchor() {
        // Sit below the production panel if it exists, else the town hall box.
        return document.getElementById('ikel-prod') ||
               document.querySelector('#townHall') ||
               (function () { var n = document.querySelectorAll('.contentBox01h'); return n.length ? n[n.length - 1] : null; })();
    }

    // Best-effort read of the current city's stock + hourly production from the
    // game's own model, so we can estimate when a level can start. If the model
    // doesn't expose it, we simply omit the time column (shows "—").
    function cityRates() {
        var stock = {}, rate = {};
        try {
            var m = window.ikariam && ikariam.model;
            if (m) {
                var map = [['wood', 'resource'], ['wine', 'tradegood1'], ['marble', 'tradegood2'],
                           ['crystal', 'tradegood3'], ['sulphur', 'tradegood4']];
                // These key names vary by game build; read numerically where present.
                stock.wood = num(m.resource); rate.wood = num(m.resourceProduction);
                stock.crystal = stock.marble = stock.sulphur = stock.wine = 0;
                if (m.tradegoodType != null) {
                    var lux = { 1: 'wine', 2: 'marble', 3: 'crystal', 4: 'sulphur' }[m.tradegoodType];
                    if (lux) { stock[lux] = num(m.tradegood); rate[lux] = num(m.tradegoodProduction); }
                }
            }
        } catch (e) {}
        return { stock: stock, rate: rate };
    }
    function num(v) { var n = parseInt(v, 10); return isNaN(n) ? 0 : n; }
    function fmt(n) { return (n || 0).toLocaleString(); }

    IKEL.register({
        id: 'construction',
        matches: function (view) { return view === 'townHall'; },
        mount: function (ctx) {
            if (document.getElementById('ikel-cm')) return;
            var anchor = findAnchor();
            if (!anchor) return;

            var panel = IKEL.el('div', { id: 'ikel-cm', 'class': 'ikel-panel' });
            panel.innerHTML = '<h3 class="ikel-header">Construction Queue</h3>' +
                '<div class="ikel-body"><div class="ikel-note">Loading queue…</div></div>';
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);

            var cid = ctx.cityId || IKEL.currentCityId();
            IKEL.api('&ikaeasy=construction' + (cid ? '&city_id=' + cid : ''))
                .then(function (r) { return r.json(); })
                .then(function (d) { render(panel, d, cid); })
                .catch(function () {
                    panel.querySelector('.ikel-body').innerHTML =
                        '<div class="ikel-note">Play through the ikabot web server to use this.</div>';
                });
        }
    });

    function render(panel, d, cid) {
        var cities = (d && d.construction) || {};
        var key = cid && cities[cid] ? cid : Object.keys(cities)[0];
        var rows = (key && cities[key]) || [];
        if (!rows.length) {
            panel.querySelector('.ikel-body').innerHTML = '<div class="ikel-note">No construction queue entries for this city.</div>';
            return;
        }
        rows.sort(function (a, b) { return (a.slot_position || 0) - (b.slot_position || 0); });

        var rt = cityRates();
        var cum = { wood: 0, wine: 0, marble: 0, crystal: 0, sulphur: 0 };
        var lastB = null, pri = 0, html = '';
        for (var i = 0; i < rows.length; i++) {
            var row = rows[i];
            var name = row.building || 'Unknown';
            if (name !== lastB) {
                var kls = name.toLowerCase().replace(/\s+/g, '_');
                html += '<tr class="ikel-group"><td colspan="8"><span class="button_building ' + kls + '" style="width:26px;height:26px;display:inline-block;vertical-align:middle;"></span> ' + esc(name) + '</td></tr>';
                lastB = name;
            }
            pri++;
            ['wood', 'wine', 'marble', 'crystal', 'sulphur'].forEach(function (k) { cum[k] += num(row[k]); });

            var hrs = 0;
            ['wood', 'wine', 'marble', 'crystal', 'sulphur'].forEach(function (k) {
                var deficit = cum[k] - (rt.stock[k] || 0);
                if (deficit > 0 && (rt.rate[k] || 0) > 0) hrs = Math.max(hrs, deficit / rt.rate[k]);
            });
            var when = timeLabel(hrs, rt);

            html += '<tr><td class="ikel-num">' + pri + '</td><td class="ikel-num">' + (row.target_level || '') + '</td>' +
                cell(cum.wood) + cell(cum.wine) + cell(cum.marble) + cell(cum.crystal) + cell(cum.sulphur) +
                '<td>' + when + '</td></tr>';
        }

        panel.querySelector('.ikel-body').innerHTML =
            '<table class="ikel-table"><thead><tr>' +
              '<th>Pri</th><th>Lvl</th><th>Wood</th><th>Wine</th><th>Marble</th><th>Crystal</th><th>Sulph</th><th>Can start</th>' +
            '</tr></thead><tbody>' + html + '</tbody></table>' +
            '<div class="ikel-note">Resource totals are cumulative across the whole queue.</div>';
    }

    function cell(v) { return '<td class="ikel-num' + (v > 0 ? '' : ' ikel-muted') + '">' + (v > 0 ? fmt(v) : '—') + '</td>'; }
    function timeLabel(hrs, rt) {
        var haveRates = Object.keys(rt.rate || {}).some(function (k) { return rt.rate[k] > 0; });
        if (!haveRates) return '—';
        if (hrs <= 0) return '<span class="ikel-ok">Now</span>';
        if (hrs < 1) return '~' + Math.ceil(hrs * 60) + ' min';
        if (hrs < 48) return '~' + Math.ceil(hrs) + ' h';
        return '~' + Math.ceil(hrs / 24) + ' d';
    }
    function esc(s) { return String(s).replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }
})();
