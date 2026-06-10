import { Research } from '../const.js';
const TYPES = ['economy', 'knowledge', 'seafaring', 'military'];

class ResearchModule {
    constructor() {
        this._data = {};
    }

    updateData(data) {
        if (data) {
            this._time = data.time;
            this._data = data.data;

            if (this._time + 86400000 < _.now()) {
                // Данные не обновлялись более суток, обновим на всякий случай
                setTimeout(() => {
                    this.forceUpdate();
                }, _.random(15, 60) * 1000);
            }
        } else {
            // Данных об исследованиях еще нет, получаем их
            this.forceUpdate();
        }
    }

    has(id) {
        if (!this._data) {
            return;
        }

        return this._data[id] > 0;
    }

    getLevel(id) {
        if (!this._data) {
            return;
        }

        return this._data[id];
    }

    update(data) {
        _.each(data, (v, name) => {
            let id = parseInt(v.aHref.match(/([0-9]+$)/)[1]);
            let level = (/explored/.test(v.liClass)) ? 1 : 0;

            if ((!level) && ([Research.Economy.ECONOMIC_FUTURE, Research.Military.MILITARISTIC_FUTURE, Research.Science.SCIENTIFIC_FUTURE, Research.Seafaring.SEAFARING_FUTURE].indexOf(id) > -1)) {
                level = (/(explored|explorable)/.test(v.liClass)) ? 1 : 0;
            }

            if (level) {
                let m = name.match(/\((\d+)\)/);
                if (m && m.length) {
                    level = parseInt(m[1]) - 1;
                }
            }

            this._data[id] = level;
        });

        this._time = _.now();
        Front.ikaeasyData.save();
    }

    toJSON() {
        return {
            time: this._time,
            data: this._data
        };
    }

    forceUpdate(callback) {
        let index = 0;

        let ajax = () => {
            if (index >= TYPES.length) {
                if (_.isFunction(callback)) {
                    callback();
                }

                return;
            }

            $.get(`/index.php?view=noViewChange&researchType=${TYPES[index]}&templateView=researchAdvisor&actionRequest=${Front.data.actionRequest}&ajax=1`, (data) => {
                data = JSON.parse(data[2][1].new_js_params).currResearchType;
                this.update(data);

                index++;
                ajax();
            }, 'json');
        };

        ajax();
    }
}

export default new ResearchModule();
