/* IkaEasy-lite feature: Tavern Manager (tavern view). */
(function () {
    'use strict';
    if (!window.IKEL) return;
    var IKEL = window.IKEL;

    var INT_KEY = 'ikel_tavern_interval';
    function getInt() { try { return parseInt(localStorage.getItem(INT_KEY), 10) || 24; } catch (e) { return 24; } }
    function setInt(v) { try { localStorage.setItem(INT_KEY, v); } catch (e) {} }

    function findAnchor() {
        return document.querySelector('#buildingPositionDiv, #tavern') ||
               (function () { var n = document.querySelectorAll('.contentBox01h'); return n.length ? n[n.length - 1] : null; })();
    }

    IKEL.register({
        id: 'tavern',
        matches: function (view) { return view === 'tavern'; },
        mount: function () {
            if (document.getElementById('ikel-tavern')) return;
            var anchor = findAnchor();
            if (!anchor) return;

            var panel = IKEL.el('div', { id: 'ikel-tavern', 'class': 'ikel-panel' });
            panel.innerHTML = '<h3 class="ikel-header">Tavern Manager</h3>' +
                '<div class="ikel-body"><div class="ikel-note">Checking ikabot…</div></div>';
            anchor.parentNode.insertBefore(panel, anchor.nextSibling);

            IKEL.api('&ikaeasy=tavern_status').then(function (r) { return r.json(); }).then(function (st) {
                if (!st || !st.available) {
                    panel.querySelector('.ikel-body').innerHTML =
                        '<div class="ikel-note">Install the <b>Tavern Manager</b> ikabot module to use this.</div>';
                    return;
                }
                render(panel);
            }).catch(function () {
                panel.querySelector('.ikel-body').innerHTML =
                    '<div class="ikel-note">Play through the ikabot web server to use this.</div>';
            });
        }
    });

    function render(panel) {
        var interval = getInt();
        panel.querySelector('.ikel-body').innerHTML =
            '<div class="ikel-warn" style="margin-bottom:6px;">&#9888; This changes the taverns in <b>every</b> city, not just this one.</div>' +
            '<div class="ikel-row"><label>Mode</label>' +
              '<button class="ikel-btn ikel-mode" data-mode="set_pct" type="button">Set %</button> ' +
              '<button class="ikel-btn ikel-mode" data-mode="zero" type="button">0%</button> ' +
              '<button class="ikel-btn ikel-mode" data-mode="max" type="button">100%</button> ' +
              '<button class="ikel-btn ikel-mode" data-mode="equilibrium" type="button">Equilibrium</button>' +
            '</div>' +
            '<div class="ikel-row" id="ikel_tsl" style="display:none;"><label>Percent</label>' +
              '<input class="ikel-range" id="ikel_tr" type="range" min="0" max="100" value="50"><span class="ikel-pct" id="ikel_tl">50%</span></div>' +
            '<div class="ikel-note" id="ikel_tdesc"></div>' +
            '<div class="ikel-row"><label>Check every</label>' +
              '<input id="ikel_ti" type="number" min="1" max="168" value="' + interval + '" style="width:52px;"> hours</div>' +
            '<div class="ikel-row"><a class="ikel-btn" id="ikel_ta" href="#">Apply</a> <span class="ikel-status" id="ikel_tstat"></span></div>';

        var $ = function (id) { return panel.querySelector('#' + id); };
        var descs = {
            set_pct: 'Sets all taverns to the slider percentage.',
            zero: 'Sets all taverns to 0% — stops wine consumption.',
            max: 'Sets all taverns to 100% — maximises growth.',
            equilibrium: 'Runs at 100% until max citizens, then the lowest wine level keeping satisfaction 5+ above zero.'
        };

        panel.querySelectorAll('.ikel-mode').forEach(function (b) {
            b.addEventListener('click', function () {
                panel.querySelectorAll('.ikel-mode').forEach(function (x) { x.style.outline = ''; });
                b.style.outline = '2px solid #6f5528';
                b.setAttribute('data-active', '1');
                panel.querySelectorAll('.ikel-mode').forEach(function (x) { if (x !== b) x.removeAttribute('data-active'); });
                var m = b.getAttribute('data-mode');
                $('ikel_tsl').style.display = (m === 'set_pct') ? '' : 'none';
                $('ikel_tdesc').textContent = descs[m] || '';
            });
        });
        $('ikel_tr').addEventListener('input', function () { $('ikel_tl').textContent = this.value + '%'; });
        $('ikel_ti').addEventListener('change', function () { setInt(this.value); });

        $('ikel_ta').addEventListener('click', function (e) {
            e.preventDefault();
            var active = panel.querySelector('.ikel-mode[data-active="1"]');
            var stat = $('ikel_tstat');
            if (!active) { stat.className = 'ikel-status ikel-err'; stat.textContent = 'Pick a mode first.'; return; }
            var mode = active.getAttribute('data-mode');
            var pct = parseInt($('ikel_tr').value, 10) || 0;
            if (mode === 'zero') { mode = 'set_pct'; pct = 0; }
            if (mode === 'max') { mode = 'set_pct'; pct = 100; }
            var interval = parseInt($('ikel_ti').value, 10) || 24;
            setInt(interval);

            stat.className = 'ikel-status'; stat.textContent = 'Applying…';
            $('ikel_ta').setAttribute('disabled', '1');
            IKEL.post({ ikaeasy_action: 'tavern_apply', mode: mode, pct: pct, interval_hrs: interval })
                .then(function (r) { return r.json(); }).then(function (d) {
                    if (d && d.ok) {
                        var n = (d.results || []).length;
                        stat.className = 'ikel-status ikel-ok';
                        stat.textContent = (d.mode === 'set_pct')
                            ? 'Done — set ' + n + ' tavern(s) to ' + pct + '%.'
                            : 'Done — equilibrium ran on ' + n + ' city(s).';
                    } else {
                        stat.className = 'ikel-status ikel-err'; stat.textContent = (d && d.error) || 'Failed.';
                    }
                }).catch(function () {
                    stat.className = 'ikel-status ikel-err'; stat.textContent = 'Could not reach ikabot.';
                }).then(function () { $('ikel_ta').removeAttribute('disabled'); });
        });
    }
})();
