import Db from '../../../helper/db.js';
import Tooltip from '../../../helper/tooltip.js';
import Parent from './dummy.js';
import { Buildings } from '../../../const.js';

const DB = Db.db();

class Module extends Parent {
    drawUpdateFreq = 60 * 1000; //1 min
    constructor(parent) {
        super(parent, 'buildings.ejs')
    }

    init() {
    }

    async getRenderData(callback) {
        const helpers = {
            getBuildingInfo: this._getBuildingInfo,
            getDiscount: this.getDiscount,
        };

        const data = {
            cities: this._cities,
            selectedCityId: this._data.cities.selectedCityId,
            manager: Front.ikaeasyData,
            buildings: {}
        };

        _.each(Buildings, (building) => {
            data.buildings[building] = Front.ikaeasyData.getBuildingInfo(building);
        });

        await callback(data, helpers);
    }

    afterRender() {
        this.updateTableWidth();
    }

    _getBuildingInfo(city, building, discount) {
        let result = [];

        _.each(city.buildings[building], (b) => {
            let info = {
                name: b.name,
                building: b.building,
                position: b.position,

                level: b.level
            };

            info.is_upgrading = !!b.completed;
            if ((b.completed) && (b.completed * 1000 < _.now())) {
                // В случае если постройка здания завершена, но город еще не посещали
                info.level += 1;
                info.is_upgrading = false;
            }

            let lvl = (info.is_upgrading) ? info.level + 1 : info.level;
            let nextLevelResources = DB.source[building][lvl];


            info.is_finished = !nextLevelResources;
            info.resources_enough = false;

            if (!info.is_finished) {
                info.resources_enough = true;
                info.resources = {};
                _.each(nextLevelResources, (v, k) => {
                    if (v) {
                        v = Math.floor(v * discount[k]);

                        info.resources[k] = {
                            amount: v,
                            enough: city.resources[k] >= v,
                            required: city.resources[k] - v
                        };

                        if (city.resources[k] < v) {
                            info.resources_enough = false;
                        }
                    }
                });
            }

            result.push(info);
        });

        return result;
    }

    // Фиксим размеры закиксифрованных ячеек чтобы скролл был только у зданий
    updateTableWidth() {
        this.$el.addClass('empire-calc-width');

        let $tr = this.$el.find('table tr:eq(0)');
        let w1 = $tr.find('th:eq(0)').width() + 4;
        let w2 = $tr.find('th:eq(1)').width();

        this.$el.css({marginLeft: w1 + w2 + 37});

        this.$el.find('th.empire_city').css('width', w1);
        this.$el.find('td.empire_city').css('width', w1 - 29); // Магическое число... методом научного тыка
        this.$el.find('.empire_transport').css('left', w1 + 28); // 16 + 12


        this.$el.removeClass('empire-calc-width');
    }

    onRegisterClickHandlers($el){
        this.onHover('.empire-building', (e) => {
            let $td = $(e.currentTarget);
            this.$el.find('.empire-building-hover').removeClass('empire-building-hover');

            if (e.type === 'mouseenter') {
                let idx = $td.index();
                _.each(this.$el.find('tr'), ($tr) => {
                    $($tr).find('td,th').eq(idx).addClass('empire-building-hover');
                });
            }
        });

        // Подготавливаем тултипы
        this.onHover('td.empire-building', async (e) => {
            let $b = $(e.currentTarget);
            if (!$b.data('data')) {
                return;
            }

            const tpl = await this.render('dummy/empire/tooltip/building', $b.data('data'));
            Tooltip.show(e, $b, $(tpl));
        });

        this.onClick('.empire-building-can-hover', (e) => {
            let $td = $(e.currentTarget);
            let cityId = $td.parent().data('id');

            Tooltip.hide();
            this.parent.close();
            this.openBuilding({
                building: $td.data('building'),
                position: $td.data('position'),
                cityId: cityId
            });
        });
    }

    getDiscount(city) {
        return city.getBuildingsCostDiscount();
    }

    onDestroy() {
        Tooltip.hide();
        Front.ikaeasyData.off('update.empireBuildings');
    }
}

export default Module;
