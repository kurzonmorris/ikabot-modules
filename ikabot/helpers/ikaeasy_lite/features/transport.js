/* IkaEasy-lite feature: RTM transport scheduling (transport view).
 * Reads the game's own transport form (destination + amounts + ship type)
 * and turns it into a recurring ikabot Resource Transport Manager schedule. */
(function () {
    'use strict';
    if (!window.IKEL) return;
    var IKEL = window.IKEL;

    var RES = ['wood', 'wine', 'marble', 'glass', 'sulfur']; // game field ids
    // Map the game's field name to the bridge's resource key.
    var BRIDGE_KEY = { wood: 'wood', wine: 'wine', marble: 'marble', glass: 'crystal', sulfur: 'sulphur' };
    var KEEP_KEY = 'ikel_rtm_keeps';
    var INT_KEY = 'ikel_rtm_interval';

    function gi(sel) { var e = document.querySelector(sel); if (!e) return 0; var n = parseInt(String(e.value).replace(/[^\d-]/g, ''), 10); return isNaN(n) ? 0 : n; }
    function loadKeeps() { try { return JSON.parse(localStorage.getItem(KEEP_KEY)) || {}; } catch (e) { return {}; } }
    function saveKeeps(k) { try { localStorage.setItem(KEEP_KEY, JSON.stringify(k)); } catch (e) {} }
    function getInterval() { try { return parseInt(localStorage.getItem(INT_KEY), 10) || 4; } catch (e) { return 4; } }
    function setInterval2(v) { try { localStorage.setItem(INT_KEY, v); } catch (e) {} }

    IKEL.register({
        id: 'transport',
        matches: function (view) { return view === 'transport'; },
        mount: function (ctx) {
            if (document.getElementById('ikel-rtm')) return;
            var anchor = document.getElementById('transportGoods');
            if (!anchor) return;

            var panel = IKEL.el('div', { id: 'ikel-rtm', 'class': 'ikel-panel' });
            panel.innerHTML = '<h3 class="ikel-header">Schedule with ikabot RTM</h3>' +
                '<div class="ikel-body"><div class="ikel-note">Checking ikabot…</div></div>';
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);

            // The RTM module is the transport backend; probe cities as the
            // availability check (bridge responds only when ikabot is up).
            IKEL.api('&ikaeasy=cities').then(function (r) {
                if (!r.ok) throw new Error();
                return r.json();
            }).then(function () { render(panel, ctx); })
            .catch(function () {
                panel.querySelector('.ikel-body').innerHTML =
                    '<div class="ikel-note">Play through the ikabot web server to use this.</div>';
            });
        }
    });

    function render(panel, ctx) {
        var keeps = loadKeeps(), interval = getInterval();
        var keepRows = RES.map(function (r) {
            return '<div class="ikel-row"><label style="min-width:64px;text-transform:capitalize;">' + r + ' keep</label>' +
                '<input type="number" min="0" id="ikel_keep_' + r + '" value="' + (keeps[r] || 0) + '" style="width:80px;"></div>';
        }).join('');

        panel.querySelector('.ikel-body').innerHTML =
            '<div class="ikel-note">Enter amounts in the transport form above, then schedule a recurring send. ' +
            '"Keep" reserves are amounts RTM leaves behind in this city.</div>' +
            keepRows +
            '<div class="ikel-row"><label style="min-width:64px;">Every</label>' +
              '<input type="number" min="1" max="168" id="ikel_rtm_int" value="' + interval + '" style="width:56px;"> hours</div>' +
            '<div class="ikel-row"><a class="ikel-btn" id="ikel_rtm_go" href="#">Schedule with RTM</a> <span class="ikel-status" id="ikel_rtm_stat"></span></div>';

        panel.addEventListener('change', function (e) {
            if (e.target.id && e.target.id.indexOf('ikel_keep_') === 0) {
                var k = loadKeeps(); k[e.target.id.replace('ikel_keep_', '')] = parseInt(e.target.value, 10) || 0; saveKeeps(k);
            }
            if (e.target.id === 'ikel_rtm_int') setInterval2(e.target.value);
        });

        panel.querySelector('#ikel_rtm_go').addEventListener('click', function (e) {
            e.preventDefault();
            schedule(panel, ctx);
        });
    }

    function schedule(panel, ctx) {
        var stat = panel.querySelector('#ikel_rtm_stat');
        var destEl = document.querySelector('input[name="destinationCityId"]');
        var dest = destEl ? parseInt(destEl.value, 10) : 0;
        var src = parseInt(ctx.cityId || IKEL.currentCityId(), 10);

        if (!src) { stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not read current city.'; return; }
        if (!dest) { stat.className = 'ikel-status ikel-err'; stat.textContent = 'Pick a destination in the form above first.'; return; }

        var resources = {}, keeps = {};
        var any = false;
        RES.forEach(function (r) {
            var amt = gi('#textfield_' + r);
            if (amt > 0) { resources[BRIDGE_KEY[r]] = amt; any = true; }
            var keep = parseInt((panel.querySelector('#ikel_keep_' + r) || {}).value, 10) || 0;
            if (keep > 0) keeps[BRIDGE_KEY[r]] = keep;
        });
        if (!any) { stat.className = 'ikel-status ikel-err'; stat.textContent = 'Enter at least one amount in the form above.'; return; }

        var shipEl = document.querySelector('#setPremiumTransports');
        var shipType = (shipEl && shipEl.checked) ? 'freighters' : 'merchant_ships';
        var interval = parseInt((panel.querySelector('#ikel_rtm_int') || {}).value, 10) || 4;

        stat.className = 'ikel-status'; stat.textContent = 'Scheduling…';
        panel.querySelector('#ikel_rtm_go').setAttribute('disabled', '1');
        IKEL.post({
            ikaeasy_action: 'schedule', source_city_id: src, dest_city_id: dest,
            resources: resources, keeps: keeps, ship_type: shipType, interval_hours: interval
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d && d.ok) {
                stat.className = 'ikel-status ikel-ok';
                stat.textContent = 'Scheduled (id ' + d.schedule_id + ') — every ' + interval + 'h.';
            } else {
                stat.className = 'ikel-status ikel-err'; stat.textContent = (d && d.error) || 'Failed.';
            }
        }).catch(function () {
            stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not reach ikabot.';
        }).then(function () { panel.querySelector('#ikel_rtm_go').removeAttribute('disabled'); });
    }
})();
