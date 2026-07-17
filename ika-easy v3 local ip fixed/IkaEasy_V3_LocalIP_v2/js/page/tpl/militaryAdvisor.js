import Db from '../../helper/db.js';
import Movement from '../../data/movement.js';
import Parent from './dummy.js';
import { Movements, Resources } from '../../const.js';
import { getInt } from '../../utils.js';

const ISLANDS = Db.getIslands();

class Page extends Parent {

    init() {
        setTimeout(async () => {
            if (this.options.get('military_movements', true)) {
                await this.movementView();
            }

            this.updateMovements();
        }, 100);

        this.ikariamPremiumToggle([$('#militaryAdvisor .premiumAccount').closest('.contentBox01h')]);
    }

    async movementView(){
        const db = Db.db();
        let $table = $('#js_MilitaryMovementsFleetMovementsTable table');

        if ($table.data('updated')) {
            return;
        }

        $table.data('updated', true);
        $table.find('td').each(function() {
            $(this).css('padding', '4px 0px').removeClass('right');
        });

        // Отображение войск и флотов в военном советнике.
        const list = $table.find('tr').has('td');
        for(let i = 0; i < list.length; i++) {
            const $tr = $(list[i]);

            if (($tr.find('.ikaeasy_transport_main').length) || (!$tr.find('.mission_icon').length)) {
                return;
            }

            let mission = $tr.find('.mission_icon').attr('class').replace('mission_icon', '').trim();
            let total = 0;
            let res_count = 0;
            let $wrapper = $('<div class="ikaeasy_transport_main"></div>');

            if ($('.unit_detail_icon', $tr).length) {
                _.each($('.unit_detail_icon', $tr), (icon) => {
                    let $icon = $(icon);
                    if ($icon.is('.resource_icon:not(.gold)')) {
                        total += getInt($icon.attr('title') || $icon.text());
                        res_count++;
                    }

                    $wrapper.append($icon);
                });

                if ((total) && (res_count)) {
                    const tpl = await this.render('military-unit-detail', {total: total, db: db});
                    $wrapper.append(tpl);
                }

                $('td', $tr).eq(3).empty().append($wrapper).attr('colspan', '2');
                $('td', $tr).eq(4).remove();
            }

            if (mission === 'piracyRaid') {
                $('td', $tr).eq(3).empty().attr('colspan', '2');
                $('td', $tr).eq(4).remove();
            }
        }

        _.each($('#js_MilitaryMovementsFleetMovementsTable .military_event_table tr'), ($tr) => {
            $tr = $($tr);

            if ($('td', $tr).eq(3).attr('colspan') !== '2') {
                let $td = $('td', $tr).eq(3);
                $td.html(`<div class="ikaeasy_transport_main_fix">${$td.html()}</div>`);
                $('td', $tr).eq(4).css('padding', '4px 10px');
            }
        });
    }

    updateMovements() {
        let activeMovements = {};
        if (!Front.viewData) {
            return;
        }

        _.each(Front.viewData.militaryAndFleetMovements, (mov, index) => {
            let stageTime = mov.eventTime * 1000;

            // Ищем movement подходящий по параметрам
            let movement = _.find(this._ieData.movements, (m) => {
                if (m.id === mov.event.id) {
                    return true;
                }

                if (m.id > 0) {
                    return false;
                }

                if ((m.originCityId !== mov.origin.cityId) || ((m.targetCityId) && (m.targetCityId !== mov.target.cityId)) || (m.transports !== mov.fleet.amount) || (m.mission !== mov.event.missionIconClass)) {
                    return false;
                }

                let stage = m.getStage();
                if ((stage) && (Math.abs(stage.finishTime - stageTime) < 5000)) {
                    return true;
                }
            });

            if (movement) {
                movement.stages = _.filter(movement.stages, (v) => {
                    if (v.time + movement.startTime > _.now()) {
                        return true;
                    }
                });

                movement.id = mov.event.id;

                movement.originCityId   = mov.origin.cityId;
                movement.originCityName = mov.origin.name;
                movement.targetCityId   = mov.target.cityId;
                movement.targetCityName = mov.target.name;

                if ((mov.event.isFleetReturning) && (movement.getStage().stage !== Movements.Stage.RETURNING)) {
                    movement.startTime = _.now();
                    movement.stages = [];
                    movement.addStage(Movements.Stage.RETURNING, mov.eventTime * 1000 - _.now());

                    if (movement.mission === Movements.Mission.TRADE) {
                        movement.resources = {};
                        movement.resources[Resources.WOOD]   = 0;
                        movement.resources[Resources.WINE]   = 0;
                        movement.resources[Resources.MARBLE] = 0;
                        movement.resources[Resources.GLASS]  = 0;
                        movement.resources[Resources.SULFUR] = 0;

                        _.each(mov.resources, (r) => {
                            let name = r.cssClass.replace('resource_icon', '').trim();
                            if (name !== 'gold') {
                                movement.resources[name] = getInt(r.amount);
                            }
                        });

                        // Обновляем список ресурсов
                    }
                }

                Front.ikaeasyData.save();

                activeMovements[movement.id] = true;
            } else {
                // Такой Movement не найден, попробуем создать его
                if ([Movements.Mission.TRADE, Movements.Mission.TRANSPORT].indexOf(mov.event.missionIconClass) === -1) {
                    // Мисия не найдена в поддерживаемых
                    return;
                }

                let data = {
                    id: mov.event.id,
                    type: (mov.isOwnArmyOrFleet) ? 'own' : 'hostile',
                    mission: mov.event.missionIconClass,

                    originCityId   : mov.origin.cityId,
                    originCityName : mov.origin.name,
                    targetCityId   : mov.target.cityId,
                    targetCityName : mov.target.name,

                    transports: mov.fleet.amount,
                    isColonize: (mov.event.mission === Movements.MissionId.COLONIZE),
                    units: null,
                    resources: {},
                    stages: []
                };

                data.resources[Resources.WOOD]   = 0;
                data.resources[Resources.WINE]   = 0;
                data.resources[Resources.MARBLE] = 0;
                data.resources[Resources.GLASS]  = 0;
                data.resources[Resources.SULFUR] = 0;

                let totalResources = 0;
                _.each(mov.resources, (r) => {
                    let name = r.cssClass.replace('resource_icon', '').trim();
                    if (name !== 'gold') {
                        data.resources[name] = getInt(r.amount);
                        totalResources += data.resources[name];
                    }
                });

                let speed = 1;
                _.each([500, 400, 300, 200, 100], (v, k) => {
                    let needTransports = Math.ceil(totalResources / v);
                    if (needTransports === data.transports) {
                        speed = 1 + k * (1 / 6)
                    }
                });

                let movement = new Movement(data);

                switch (mov.event.missionState) {
                    case Movements.MissionState.LOADING: // Погрузка
                        movement.addStage(Movements.Stage.LOADING, mov.eventTime * 1000 - _.now());
                        movement.addStage(Movements.Stage.EN_ROUTE, this.getTravelTime(mov.origin.islandId, mov.target.islandId) * speed);
                        break;

                    case Movements.MissionState.EN_ROUTE: // В пути
                        movement.addStage(Movements.Stage.EN_ROUTE, mov.eventTime * 1000 - _.now());
                        break;
                }

                Front.ikaeasyData.addMovement(movement);
                activeMovements[movement.id] = true;
            }
        });


        // Удаляем те движения которых нет в списке
        if ($('#militaryMovementsFleetMovementsFilters [data-filter="all"].selected').length) {
            let ids = this._ieData.movements.map((v) => { return v.id; });
            _.each(ids, (id) => {
                if (!activeMovements[id]) {
                    Front.ikaeasyData.removeMovement(id);
                }
            });
        }
    }

    getTravelTime(islandId1, islandId2) {
        let island1 = this.findIsland(islandId1);
        let island2 = this.findIsland(islandId2);
        if ((island1.x === island2.x) && (island1.y === island2.y)) {
            return 600000;
        }

        let x = island1.x - island2.x;
        let y = island1.y - island2.y;
        let math = 1200 * Math.sqrt(Math.pow(x, 2) + Math.pow(y, 2));
        return math * 1000;
    }

    findIsland(islandId) {
        let islandX = null;
        let islandY = null;
        let isFinded = false;
        _.each(ISLANDS, (xx, x) => {
            _.each(xx, (island, y) => {
                if (island[0] === islandId) {
                    isFinded = true;
                    islandX = x;
                    islandY = y;
                    return false;
                }
            });

            if (isFinded) {
                return false;
            }
        });

        return {x: islandX, y: islandY};
    }

    destroy() {
        this._movementInterval && clearInterval(this._movementInterval);
    }
}

export default Page;
