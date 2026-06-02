import Parent from './dummy.js';
import { Resources, Movements } from '../../const.js';
import { getInt, parseTimeString } from '../../utils.js';

class Page extends Parent {

    init() {
        this.updateMovements();
    }

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
                let resList = [Resources.WOOD, Resources.WINE, Resources.MARBLE, Resources.SULFUR, Resources.GLASS];

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

            // Ищем movement подходящий по параметрам
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

            // Удаляем такой маршрут
            Front.ikaeasyData.removeMovement(eventId);
        });
    }
}

export default Page;
