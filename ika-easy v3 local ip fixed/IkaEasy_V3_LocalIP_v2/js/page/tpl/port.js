import Parent from './dummy.js';
import { Resources, Movements } from '../../const.js';
import { getInt, parseTimeString, formatNumber } from '../../utils.js';

const RTM_KEEP_KEY = 'ikaeasy_rtm_keeps';

const RES_NAMES = {
    wood:   'Wood',
    wine:   'Wine',
    marble: 'Marble',
    glass:  'Crystal',
    sulfur: 'Sulfur',
};

class Page extends Parent {

    init() {
        this.updateMovements();
        this.addRtmSection();
    }

    // ── existing movement tracker ──────────────────────────────────────────

    updateMovements() {
        let $trs = $('#tabSendTransporter table.table01:eq(0) > tbody > tr');
        _.each($trs, ($tr, index) => {
            $tr = $($tr);

            if ((index === 0) || ($tr.find('td').length < 4)) {
                return;
            }

            let targetCityName = $tr.find('td.destination').text().trim();
            let transports = getInt($tr.find('td:eq(1) > span').text());
            let time = parseTimeString($tr.find('td.status .time:eq(0)').text());

            let $cargo = $tr.find('td:eq(1) > .tooltip tr');
            let resources = {};
            if ($cargo.length > 1) {
                _.each($cargo, ($ctr, k) => {
                    $ctr = $($ctr);
                    if (k === 0) {
                        return;
                    }

                    let icon = $ctr.find('td:eq(0) img').attr('src').match(/icon_(.*?)\.png/)[1];
                    resources[icon] = getInt($ctr.find('td:eq(1)').text());
                });
            }

            let eventId = null;
            if ($tr.find('td a.action_icon').length) {
                eventId = parseInt($tr.find('td a.action_icon').attr('href').match(/eventId=([0-9]+)/)[1]);
            }

            let tmpCnt = 0;
            let movement = _.find(this._ieData.movements, (m) => {
                if (m.id === eventId) {
                    return true;
                }

                if ((m.originCityId !== this._city.cityId) || (m.targetCityName !== targetCityName) || (m.transports !== transports)) {
                    return false;
                }

                let stage = m.getStage();
                let stageTime = _.now() + time;
                if ((stage) && (stage.stage === Movements.Stage.LOADING)) {
                    tmpCnt++;
                    if ((eventId) && (m.id > 0)) {
                        return false;
                    }

                    if ((isNaN(time)) && (tmpCnt === index)) {
                        return true;
                    } else if (Math.abs(stage.finishTime - stageTime) < 5000){
                        return true;
                    }
                }
            });

            if ((movement) && (eventId)) {
                movement.stages = _.filter(movement.stages, (v) => {
                    if (v.time + movement.startTime > _.now()) {
                        return true;
                    }
                });

                movement.id = eventId;
                Front.ikaeasyData.save();
            }
        });

        $trs.find('a.action_icon.abort').click((e) => {
            e.preventDefault();
            let $a = $(e.currentTarget);
            let href = $a.attr('href');
            let eventId = parseInt(href.match(/eventId=([0-9]+)/)[1]);
            Front.ikaeasyData.removeMovement(eventId);
        });
    }

    // ── RTM panel ──────────────────────────────────────────────────────────

    async addRtmSection() {
        if ($('#ikaeasy_rtm_wrap').length) {
            return;
        }

        const cities    = this._prepareCityList();
        const resources = this._getCurrentResources();
        const keeps     = this._loadKeeps();

        let ikabotDetected = false;
        try {
            const r = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=cities`,
                { credentials: 'include', signal: AbortSignal.timeout(2000) }
            );
            if (r.ok) {
                ikabotDetected = true;
            }
        } catch (_) {}

        const tpl    = await this.render('port-rtm', { cities, resources, keeps, ikabotDetected, resNames: RES_NAMES });
        const $panel = $(tpl);
        $panel.attr('id', 'ikaeasy_rtm_wrap');
        $('#tabSendTransporter').append($panel);

        this._bindRtmEvents($panel, resources);
    }

    _prepareCityList() {
        let list = [];
        const selfId = this._data.cities.selectedCityId;
        _.each(this._data.cities, (city, key) => {
            if (key.indexOf('city_') !== 0) {
                return;
            }
            if (parseInt(city.id) === selfId) {
                return;
            }
            list.push({ id: parseInt(city.id), name: city.name, isOwn: true });
        });
        return list;
    }

    _getCurrentResources() {
        let res = {};
        const city = this._city;
        ['wood','wine','marble','glass','sulfur'].forEach((r) => {
            res[r] = Math.floor(city.resources ? (city.resources[r] || 0) : 0);
        });
        return res;
    }

    _loadKeeps() {
        try {
            return JSON.parse(localStorage.getItem(RTM_KEEP_KEY) || '{}');
        } catch (_) {
            return {};
        }
    }

    _saveKeeps(keeps) {
        localStorage.setItem(RTM_KEEP_KEY, JSON.stringify(keeps));
    }

    _sendable(res, resources, keeps) {
        return Math.max(0, (resources[res] || 0) - (parseInt(keeps[res]) || 0));
    }

    _bindRtmEvents($panel, resources) {
        const keeps = this._loadKeeps();
        const self  = this;

        $('#ikaeasy_rtm_enable', $panel).on('change', function () {
            $('#ikaeasy_rtm_panel', $panel).toggle(this.checked);
        });

        function updateRow($row, res) {
            const k   = parseInt($('.ikaeasy-rtm-keep[data-res="' + res + '"]', $row).val()) || 0;
            const max = self._sendable(res, resources, { [res]: k });
            const $sl = $('.ikaeasy-rtm-slider[data-res="' + res + '"]', $row);
            const cur = Math.min(parseInt($sl.val()) || 0, max);

            $sl.attr('max', max).val(cur);
            $('.ikaeasy-rtm-sendval', $row).text(formatNumber(cur));
        }

        $panel.on('input', '.ikaeasy-rtm-keep', function () {
            const res  = $(this).data('res');
            const $row = $(this).closest('tr');
            keeps[res] = parseInt($(this).val()) || 0;
            self._saveKeeps(keeps);
            updateRow($row, res);
        });

        $panel.on('input', '.ikaeasy-rtm-slider', function () {
            const $row = $(this).closest('tr');
            $('.ikaeasy-rtm-sendval', $row).text(formatNumber(parseInt($(this).val()) || 0));
        });

        $panel.on('click', '.ikaeasy-rtm-btn', function (e) {
            e.preventDefault();
            const res  = $(this).data('res');
            const mode = $(this).data('mode');
            const $row = $(this).closest('tr');
            const k    = parseInt($('.ikaeasy-rtm-keep[data-res="' + res + '"]', $row).val()) || 0;
            const max  = self._sendable(res, resources, { [res]: k });
            const $sl  = $('.ikaeasy-rtm-slider[data-res="' + res + '"]', $row);

            let val;
            if (mode === '100k')    val = Math.min(100000, max);
            else if (mode === '1m') val = Math.min(1000000, max);
            else                    val = max;

            $sl.val(val);
            $('.ikaeasy-rtm-sendval', $row).text(formatNumber(val));
        });

        $('#ikaeasy_rtm_sendnow', $panel).on('click', (e) => {
            e.preventDefault();
            this._sendNow($panel);
        });

        if ($('#ikaeasy_rtm_schedule', $panel).length) {
            $('#ikaeasy_rtm_schedule', $panel).on('click', (e) => {
                e.preventDefault();
                this._scheduleWithRtm($panel, keeps);
            });
        }
    }

    _gatherAmounts($panel) {
        let amounts = {};
        $('.ikaeasy-rtm-slider', $panel).each(function () {
            amounts[$(this).data('res')] = parseInt($(this).val()) || 0;
        });
        return amounts;
    }

    _sendNow($panel) {
        const amounts  = this._gatherAmounts($panel);
        const destId   = parseInt($('#ikaeasy_rtm_dest', $panel).val());
        if (!destId) {
            return;
        }

        const srcId = this._data.cities.selectedCityId;
        let query = `view=transport&destinationCityId=${destId}&currentCityId=${srcId}`;
        let hasAny = false;
        Object.entries(amounts).forEach(([res, amt]) => {
            if (amt > 0) {
                hasAny = true;
                query += `&${res}=${amt}`;
            }
        });

        if (!hasAny) {
            return;
        }

        ajaxHandlerCall(`/index.php?${query}`);
    }

    async _scheduleWithRtm($panel, keeps) {
        const amounts  = this._gatherAmounts($panel);
        const destId   = parseInt($('#ikaeasy_rtm_dest', $panel).val());
        const shipType = $('input[name="ikaeasy_rtm_shiptype"]:checked', $panel).val();
        const interval = parseInt($('#ikaeasy_rtm_interval', $panel).val()) || 4;
        const srcId    = this._data.cities.selectedCityId;
        const $status  = $('#ikaeasy_rtm_status', $panel);

        if (!destId) {
            return;
        }

        $status.text('Scheduling…').css('color', '');

        try {
            const resp = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy`,
                {
                    method:      'POST',
                    credentials: 'include',
                    headers:     { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ikaeasy_action:  'schedule',
                        source_city_id:  srcId,
                        dest_city_id:    destId,
                        resources:       amounts,
                        keeps:           keeps,
                        ship_type:       shipType,
                        interval_hours:  interval,
                    }),
                }
            );

            const data = await resp.json();
            if (data.ok) {
                $status.css('color', 'green').text('Scheduled — route added to RTM.');
            } else {
                $status.css('color', 'red').text(data.error || 'Failed.');
            }
        } catch (_) {
            $status.css('color', 'red').text('Could not reach ikabot.');
        }
    }
}

export default Page;
