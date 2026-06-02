import Movement from '../../data/movement.js';
import Parent from './dummy.js';
import { getInt, parseTimeString } from '../../utils.js';
import { Resources, Movements } from '../../const.js';


class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle(['#setPremiumTransports']);

        this.moveTransportBtn($('.minusPlusValueOuterContainer'));
        this.removeIkariamButtons();
        await this.updateMinMaxButtons($('#transportGoods'));

        this.updateMovements();
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
}

export default Page;
