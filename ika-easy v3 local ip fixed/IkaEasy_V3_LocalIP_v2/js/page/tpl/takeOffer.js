import Movement from '../../data/movement.js';
import Parent from './transport.js';
import { getInt, parseTimeString } from '../../utils.js';
import { Movements, Resources } from '../../const.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle(['#setPremiumTransports']);

        this.moveTransportBtn($('#mission .costsTotal'));
        this.addTransportButtons();
        this.updateMovements();
    }

    async addTransportButtons() {
        let $container = $('#transportForm');
        if ($container.data('updated')) {
            return;
        }

        if (this.options.get('transport_buttons', true)) {
            $('#transportForm').addClass('ikaeasy-transport-wrap ikaeasy-transport-bnt-no-margin');

            const list = $('#transportForm td.input input');
            for(let i = 0; i < list.length; i++) {
                const node = $(list[i]);
                const $a = $('a[href="#setmax"]', node.parent());

                const tpl = await this.render('transport-buttons', {custom: [{ val: 999999999, text: $a.text() }]});
                const $box = $(tpl);
                node.after($box);

                $('.ikaeasy_transport_button_trigger', $box).click((e) => {
                    const $el = $(e.currentTarget);
                    this.clickButton(parseInt($el.attr('data-sum')), node)
                });

                $a.remove();
            }
        }

        $container.data('updated', true);
    }

    updateMovements() {
        let $form = $('#transportForm');
        $form.on('submit', () => {
            let elements = $form[0].elements;
            let data = {
                id: -_.now(),
                type: 'own',
                mission: Movements.Mission.TRANSPORT,
                originCityId: this._city.cityId,
                targetCityId : parseInt(elements['destinationCityId'].value),
                transports: getInt($('#transporterCount').val()) || 0,
                units: null,
                resources: {},
                stages: []
            };


            data.resources[Resources.WOOD]   = getInt($('#textfield_resource').val())   || 0;
            data.resources[Resources.WINE]   = getInt($('#textfield_tradegood1').val()) || 0;
            data.resources[Resources.MARBLE] = getInt($('#textfield_tradegood2').val()) || 0;
            data.resources[Resources.GLASS]  = getInt($('#textfield_tradegood3').val()) || 0;
            data.resources[Resources.SULFUR] = getInt($('#textfield_tradegood4').val()) || 0;

            let totalResources = data.resources[Resources.WOOD] + data.resources[Resources.WINE] + data.resources[Resources.MARBLE] + data.resources[Resources.GLASS] + data.resources[Resources.SULFUR];
            let loadingFinished = (totalResources / this._city.getLoadingSpeed() * Time.MILLIS_PER_SECOND);

            let movement = new Movement(data);

            if (elements['function'].value === 'buyGoodsAtAnotherBranchOffice') {
                // Покупка товаров
                movement.mission = Movements.Mission.TRADE;

                // Добавляем сначала стадию EN_ROUTE (сначала сухогрузы пустыми плывут в город за ресурсами)
                movement.addStage(Movements.Stage.EN_ROUTE, parseTimeString($('#journeyTime').text().trim()));
            } else {
                movement.addStage(Movements.Stage.LOADING, loadingFinished);
            }

            movement.addStage(Movements.Stage.EN_ROUTE, parseTimeString($('#journeyTime').text().trim()));

            Front.on('form', (response) => {
                Front.off('form');

                if ((!response[3][1]) || (!response[3][1].length) || (!response[3][1][0]) || (response[3][1][0].type !== 10)) {
                    return;
                }

                Front.ikaeasyData.addMovement(movement);
            });
        });
    }

}

export default Page;
