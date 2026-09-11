/* IkaEasy-lite feature: Resource Production Manager (town hall view). */
(function () {
    'use strict';
    if (!window.IKEL) return;
    var IKEL = window.IKEL;

    var LS = {
        scope: 'ikel_prod_scope', mode: 'ikel_prod_mode',
        wood: 'ikel_prod_wood', lux: 'ikel_prod_lux', oc: 'ikel_prod_oc'
    };
    function get(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
    function set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

    function findAnchor() {
        var sel = document.querySelector('#townHall') ||
                  (function () { var n = document.querySelectorAll('.contentBox01h'); return n.length ? n[n.length - 1] : null; })();
        return sel;
    }

    IKEL.register({
        id: 'production',
        matches: function (view) { return view === 'townHall'; },
        mount: function (ctx) {
            if (document.getElementById('ikel-prod')) return;
            var anchor = findAnchor();
            if (!anchor) return;

            var panel = IKEL.el('div', { id: 'ikel-prod', 'class': 'ikel-panel' });
            panel.innerHTML =
                '<h3 class="ikel-header">Production Manager</h3>' +
                '<div class="ikel-body"><div class="ikel-note">Checking ikabot…</div></div>';
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);

            IKEL.api('&ikaeasy=prod_status').then(function (r) { return r.json(); }).then(function (st) {
                if (!st || !st.available) {
                    panel.querySelector('.ikel-body').innerHTML =
                        '<div class="ikel-note">Install the <b>Resource Production Manager</b> ikabot module (main menu &rarr; option 30) to use this.</div>';
                    return;
                }
                render(panel, ctx);
            }).catch(function () {
                panel.querySelector('.ikel-body').innerHTML =
                    '<div class="ikel-note">Play through the ikabot web server to use this.</div>';
            });
        }
    });

    function render(panel, ctx) {
        var scope = get(LS.scope, 'city'), mode = get(LS.mode, 'wood_then_luxury');
        var wood = parseInt(get(LS.wood, '100'), 10), lux = parseInt(get(LS.lux, '100'), 10);
        var oc = get(LS.oc, '0') === '1';

        panel.querySelector('.ikel-body').innerHTML =
            '<div class="ikel-row"><label>Apply to</label>' +
              '<label><input type="radio" name="ikel_ps" value="city"' + (scope === 'city' ? ' checked' : '') + '> This city</label>' +
              '<label><input type="radio" name="ikel_ps" value="all"' + (scope === 'all' ? ' checked' : '') + '> All cities <span class="ikel-warn">&#9888;</span></label>' +
            '</div>' +
            '<div class="ikel-row"><label>Mode</label>' +
              '<select id="ikel_pm">' +
                opt('wood', 'Wood only', mode) + opt('luxury', 'Luxury only', mode) +
                opt('wood_then_luxury', 'Wood, then Luxury', mode) + opt('luxury_then_wood', 'Luxury, then Wood', mode) +
              '</select></div>' +
            '<div class="ikel-row" id="ikel_pw"><label>Wood %</label>' +
              '<input class="ikel-range" id="ikel_pwr" type="range" min="0" max="100" value="' + wood + '"><span class="ikel-pct" id="ikel_pwl">' + wood + '%</span></div>' +
            '<div class="ikel-row" id="ikel_pl"><label>Luxury %</label>' +
              '<input class="ikel-range" id="ikel_plr" type="range" min="0" max="100" value="' + lux + '"><span class="ikel-pct" id="ikel_pll">' + lux + '%</span></div>' +
            '<div class="ikel-row"><label>Overcharge</label>' +
              '<label><input type="checkbox" id="ikel_po"' + (oc ? ' checked' : '') + '> Overcharge luxury at 100% (costs gold)</label></div>' +
            '<div class="ikel-row"><a class="ikel-btn" id="ikel_pa" href="#">Apply</a> <span class="ikel-status" id="ikel_pstat"></span></div>';

        var $ = function (id) { return panel.querySelector('#' + id); };

        function refreshRows() {
            var m = $('ikel_pm').value;
            $('ikel_pw').style.display = (m === 'wood' || m === 'wood_then_luxury' || m === 'luxury_then_wood') ? '' : 'none';
            $('ikel_pl').style.display = (m === 'luxury' || m === 'wood_then_luxury' || m === 'luxury_then_wood') ? '' : 'none';
        }
        refreshRows();

        panel.addEventListener('change', function (e) {
            if (e.target.name === 'ikel_ps') set(LS.scope, e.target.value);
            if (e.target.id === 'ikel_pm') { set(LS.mode, e.target.value); refreshRows(); }
            if (e.target.id === 'ikel_po') set(LS.oc, e.target.checked ? '1' : '0');
        });
        $('ikel_pwr').addEventListener('input', function () { $('ikel_pwl').textContent = this.value + '%'; set(LS.wood, this.value); });
        $('ikel_plr').addEventListener('input', function () { $('ikel_pll').textContent = this.value + '%'; set(LS.lux, this.value); });

        $('ikel_pa').addEventListener('click', function (e) {
            e.preventDefault();
            apply(panel, ctx);
        });
    }

    function opt(v, label, cur) { return '<option value="' + v + '"' + (v === cur ? ' selected' : '') + '>' + label + '</option>'; }

    function apply(panel, ctx) {
        var $ = function (id) { return panel.querySelector('#' + id); };
        var stat = $('ikel_pstat');
        var scope = (panel.querySelector('input[name="ikel_ps"]:checked') || {}).value || 'city';
        var mode = $('ikel_pm').value;
        var wood = parseInt($('ikel_pwr').value, 10) || 100;
        var lux = parseInt($('ikel_plr').value, 10) || 100;
        var oc = $('ikel_po').checked;

        stat.className = 'ikel-status'; stat.textContent = 'Applying…';
        $('ikel_pa').setAttribute('disabled', '1');

        function send(cityIds) {
            return IKEL.post({
                ikaeasy_action: 'modify_production', city_ids: cityIds,
                mode: mode, wood_pct: wood, luxury_pct: lux, overcharge: oc
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d && d.ok) {
                    var ok = (d.results || []).filter(function (x) { return x.ok; }).length;
                    stat.className = 'ikel-status ikel-ok';
                    stat.textContent = 'Done — ' + ok + ' of ' + (d.results || []).length + ' city(s) updated.';
                } else {
                    stat.className = 'ikel-status ikel-err';
                    stat.textContent = (d && d.error) || 'Failed.';
                }
            });
        }

        var done = function () { $('ikel_pa').removeAttribute('disabled'); };

        if (scope === 'all') {
            IKEL.api('&ikaeasy=cities').then(function (r) { return r.json(); }).then(function (d) {
                var ids = (d.cities || []).map(function (c) { return parseInt(c.id, 10); }).filter(Boolean);
                return send(ids);
            }).catch(function () {
                stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not reach ikabot.';
            }).then(done);
        } else {
            var cid = ctx.cityId || IKEL.currentCityId();
            if (!cid) { stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not read current city.'; done(); return; }
            send([parseInt(cid, 10)]).catch(function () {
                stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not reach ikabot.';
            }).then(done);
        }
    }
})();
