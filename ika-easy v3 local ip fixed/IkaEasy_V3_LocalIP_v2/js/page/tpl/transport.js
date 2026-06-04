import Movement from '../../data/movement.js';
import Parent from './dummy.js';
import { getInt, parseTimeString } from '../../utils.js';
import { Resources, Movements } from '../../const.js';

const RTM_KEEP_KEY = 'ikaeasy_rtm_keeps';

const RES_NAMES = {
    wood:   'Wood',
    wine:   'Wine',
    marble: 'Marble',
    glass:  'Crystal',
    sulfur: 'Sulfur',
};

class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle(['#setPremiumTransports']);

        this.moveTransportBtn($('.minusPlusValueOuterContainer'));
        this.removeIkariamButtons();
        await this.updateMinMaxButtons($('#transportGoods'));

        this.updateMovements();
        await this.addRtmSection();
    }

    updateMovements() {
        let $form = $('#transportForm, #transport');
        $form.on('submit', () => {
            let data = {
                id: -_.now(),
                type: 'own',
                mission: Movements.Mission.TRANSPORT,
                originCityId: this._city.cityId,
                originCityName: this._city.name,
                transports: getInt($('#transporterCount').val()) || 0,
                units: null,
                resources: {},
                stages: []
            };

            if ($form.find('input[name="destinationCityId"]').length) {
                data.targetCityId = parseInt($form.find('input[name="destinationCityId"]').val());
                data.targetCityName = $form.find('.journeyTarget').html().replace(/(<span.*?<\/span>)/, '').trim();
            }

            data.resources[Resources.WOOD]   = getInt($('#textfield_wood').val())   || 0;
            data.resources[Resources.WINE]   = getInt($('#textfield_wine').val())   || 0;
            data.resources[Resources.MARBLE] = getInt($('#textfield_marble').val()) || 0;
            data.resources[Resources.GLASS]  = getInt($('#textfield_glass').val())  || 0;
            data.resources[Resources.SULFUR] = getInt($('#textfield_sulfur').val()) || 0;

            if ($('#createColony').length) {
                data.isColonize = true;
                data.resources[Resources.WOOD] += 1250;
            }

            const loadingFinished = parseTimeString($('#loadingTime').text().trim());
            const routeTime = parseTimeString($('#journeyTime').text().trim());

            Front.on('form', (response) => {
                Front.off('form');

                _.each(response, (r) => {
                     if ((r[0] === 'provideFeedback') && (r[1][0].type === 10)) {
                         let movement = new Movement(data);
                         movement.addStage(Movements.Stage.LOADING, loadingFinished);
                         movement.addStage(Movements.Stage.EN_ROUTE, routeTime);

                         Front.ikaeasyData.addMovement(movement);
                         return false;
                     }
                });
            });
        });
    }

    moveTransportBtn($container) {
        if ($('#ikaeasy_tranport_btn').length) {
            return;
        }

        let $btn = $('#submit').parent().clone();
        $btn.find('input').attr('id', 'ikaeasy_tranport_btn');

        $container.after($btn);
        $container.after($('#missionSummary'));
        $container.after('<hr/>');
    }

    removeIkariamButtons() {
        if (this.options.get('transport_buttons', true)) {
            $('.minusPlusValueContainer').remove();
            $('#transportGoods').addClass('ikaeasy-transport-wrap');
            $('.button.minus', '.resourceAssign').remove();
            $('.button.plus', '.resourceAssign').remove();
        }
    }

    async updateMinMaxButtons($container) {
        if ($container.data('updated')) {
            return;
        }

        const list = $('ul.resourceAssign li', $container);
        for(let i = 0; i < list.length; i++) {
            const $el = $(list[i]);

            if (this.options.get('transport_buttons', true)) {
                await this.addTransportButtons($el);
            }

            let $setMax = $('a.setMax', $el).clone().removeAttr('id').replaceAll($('a.setMax', $el));
            let $setMin = $('a.setMin', $el).clone().removeAttr('id').replaceAll($('a.setMin', $el));

            $setMax.click((e) => {
                e.preventDefault();

                let $input = $('input', $el);
                let prevVal = parseInt($input.val());
                $input.val('999999999').focus().blur();

                let val = parseInt($input.val());
                if ((val % 500 !== 0) && (prevVal + 500 < val)) {
                    this.clickButton(-500, $input);
                }
            });

            $setMin.click((e) => {
                e.preventDefault();

                let $input = $('input', $el);
                let val = parseInt($input.val());

                if ((val > 500) && (val % 500 !== 0)) {
                    val -= (val % 500);
                } else {
                    val = 0;
                }

                $input.val(val).focus().blur();
            });
        }

        this.addButtonsEvent();
        $container.data('updated', true);
    }

    addButtonsEvent() {
        $('.ikaeasy_transport_button_trigger').click((e) => {
            let $el = $(e.currentTarget);
            let $input = $el.closest('.sliderinput').find('input');

            this.clickButton(parseInt($el.attr('data-sum')), $input)
        });
    }

    async addTransportButtons($el) {
        if ($el.find('.ikaeasy_resource').length) {
            return;
        }

        const tpl = await this.render('transport-buttons');
        const $box = $(tpl);
        $('div.sliderinput', $el).prepend($box);
    }

    clickButton(sum, $input) {
        let val = parseInt($input.val());

        if (sum === -500) {
            if ((val > 0) && (val % 500 !== 0)) {
                val -= (val % 500);
            } else {
                val += sum;
            }
        } else if (val % 500 === 0) {
            val += sum;
        } else {
            if (val % sum !== 0) {
                val += sum - (val % sum);
            } else {
                val += sum;
            }
        }

        $input.val(val).focus().blur();
    }

    // ── RTM inline scheduling ────────────────────────────────────────────────

    async addRtmSection() {
        if ($('#ikaeasy_rtm_inline').length) {
            return;
        }

        let ikabotDetected = false;
        try {
            const r = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=cities`,
                { credentials: 'include', signal: AbortSignal.timeout(2000) }
            );
            if (r.ok) ikabotDetected = true;
        } catch (_) {}

        const keeps = this._loadKeeps();
        const resources = this._getCurrentResources();

        const tpl = await this.render('transport-rtm', { keeps, resources, resNames: RES_NAMES, ikabotDetected });
        const $section = $(tpl);
        $section.attr('id', 'ikaeasy_rtm_inline');

        // Insert after the resource assignment block, before the submit button area.
        const $anchor = $('#transportGoods');
        if ($anchor.length) {
            $anchor.after($section);
        } else {
            $('#transportForm, #transport').append($section);
        }

        if (ikabotDetected) {
            this._bindRtmEvents($section, keeps);
        }
    }

    _getCurrentResources() {
        const res = {};
        ['wood','wine','marble','glass','sulfur'].forEach((r) => {
            const v = getInt($(`#textfield_${r}`).val());
            const $li = $(`#textfield_${r}`).closest('li');
            const avail = getInt($li.find('.value, .current').first().text()) || 0;
            res[r] = avail;
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

    _bindRtmEvents($section, keeps) {
        $section.on('input', '.ikaeasy-rtm-keep', function () {
            keeps[$(this).data('res')] = parseInt($(this).val()) || 0;
            localStorage.setItem(RTM_KEEP_KEY, JSON.stringify(keeps));
        });

        $section.on('click', '#ikaeasy_rtm_schedule_btn', (e) => {
            e.preventDefault();
            this._scheduleWithRtm($section, keeps);
        });
    }

    async _scheduleWithRtm($section, keeps) {
        const $status  = $('#ikaeasy_rtm_status', $section);
        const $form    = $('#transportForm, #transport');
        const destId   = parseInt($form.find('input[name="destinationCityId"]').val());
        const srcId    = this._data.cities.selectedCityId || this._city.cityId;
        const interval = parseInt($('#ikaeasy_rtm_interval', $section).val()) || 4;

        // Read current amounts from the transport form's own inputs.
        const amounts = {};
        ['wood','wine','marble','glass','sulfur'].forEach((r) => {
            amounts[r] = getInt($(`#textfield_${r}`).val()) || 0;
        });

        // Ship type: freighters if premium transport is enabled.
        const shipType = $('#setPremiumTransports').is(':checked') ? 'freighters' : 'merchant_ships';

        if (!destId) {
            $status.css('color', 'red').text('No destination selected.');
            return;
        }
        if (!Object.values(amounts).some(v => v > 0)) {
            $status.css('color', 'red').text('Set at least one resource amount above.');
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
                $status.css('color', 'green').text(`Scheduled — route #${data.schedule_id} added to RTM.`);
            } else {
                $status.css('color', 'red').text(data.error || 'Failed.');
            }
        } catch (_) {
            $status.css('color', 'red').text('Could not reach ikabot.');
        }
    }
}

export default Page;
