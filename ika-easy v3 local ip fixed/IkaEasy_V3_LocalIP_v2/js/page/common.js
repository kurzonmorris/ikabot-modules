import Render from '../helper/templater.js';
import Controller from './controller.js';
import { execute_js, getItem, setItem, removeItem } from '../utils.js';

class Common extends Controller{
    constructor() {
        super();
    }

    /**
     * Смена города без обновления страницы
     * @param city_id {Number}
     */
    changeCity(city_id) {
        $('#js_cityIdOnChange').val(city_id);
        execute_js('ajaxHandlerCallFromForm(document.getElementById("changeCityForm"));');
    }

    /**
     * Рендер шаблонов
     * @param tpl {String} имя файла шаблона
     * @param data {Object} данные для шаблона
     * @param helpers {Object} хелперсы для шаблона
     * @returns {Promise<String>}
     */
    async render(tpl, data = {}, helpers = {}) {
        return await Render(tpl, data, helpers)
    }

    /**
     * Открыть нужное здание
     * @param params
     */
    openBuilding(params) {
        removeItem('open_building_callback');

        let b = params || getItem('open_building') || {};
        if ((!b.cityId) || (!b.building) || (typeof b.position === 'undefined')) {
            removeItem('open_building');
            return;
        }

        if ((params) && (Front.bg !== 'city')) {
            location.href = `/index.php?view=${ b.building }&cityId=${ b.cityId }&position=${ b.position }&backgroundView=city&currentCityId=${ b.cityId }`;
            return;
        }

        if (this.getCityId() !== b.cityId) {
            if (params) {
                setItem('open_building', params, 5);
            }

            this.changeCity(b.cityId);
        } else {
            if (b.callback) {
                setItem('open_building_callback', b, 5);
            }

            execute_js(`ajaxHandlerCall('?view=${b.building}&cityId=${b.cityId}&position=${b.position}&actionRequest=${this._data.actionRequest}');`);
            removeItem('open_building');
        }
    }

    /**
     * Запуск постройки здания
     * @param position
     * @param level
     * @param cityId
     */
    upgradeBuilding(position, level, cityId = null) {
        cityId = cityId || this.getCityId();
        execute_js(`ajaxHandlerCall('/index.php?action=CityScreen&function=upgradeBuilding&actionRequest=${this._data.actionRequest}&currentCityId=${cityId}&cityId=${cityId}&position=${position}&level=${level}&backgroundView=city');`);
    }
}

export default Common;
