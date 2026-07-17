import Options from '../helper/options.js';

class Controller {
    constructor() {
        this._ieData = Front.ikaeasyData;
        this._modules = {};
        this._updateData();

        this.options = Options;
    }
    _ajaxMode = false;

    set ajaxHTML(rawHTML){
        // not recommended to use with null or false
        // but just in case you will need to disable ajax mode
        if(rawHTML === null || typeof rawHTML === Boolean){
            this._ajaxHTML = false
            this._ajaxMode = false;
        }
        if(rawHTML === '' || rawHTML.length === 0){
            console.error("IkaEasy: AjaxHTML is empty");
        }
        try{
            const htmlParsed = $.parseHTML(rawHTML);
            this._ajaxHTML = $(htmlParsed);
            this._ajaxMode = true;
        }catch(err){
            console.errror(`IkaEasy: ${err}`);
        }
    }
    get ajaxHTML(){
        return this._ajaxHTML;
    }
    _ajaxHTML = null;

    DOMselect(query){
        return this._ajaxMode ? this.ajaxHTML.find(query) : $(query);
    }

    refresh() {
        _.each(this._modules, (module) => {
            module.refresh();
            module.updated();
        });

        this._updateData();
    }

    _updateData() {
        this._data = Front.data;
        this._prepareCities();
    }

    get _city() {
        return this._overrideCity || this._ieData.getCurrentCity();
    }
    setCurrentCity(cityId){
        this._overrideCity = this._ieData.getCity(cityId);
    }
    _overrideCity = null;

    _prepareCities() {
        let cities = [];
        _.each(this._data.cities, (city, key) => {
            if (key.indexOf('city_') === 0) {
                city.id = parseInt(city.id);
                cities.push(city);
            }
        });

        this._cities = Object.freeze(cities);
    }

    /**
     * Получить  cityId
     * @returns {Number} cityId
     */
    getCityId() {
        if ((this._data.city) && (this._data.city.id)) {
            return parseInt(this._data.city.id);
        }

        return this._data.cities.selectedCityId;
    }

    /**
     * Создать модуль
     * @param name {String} имя файла модуля
     * @returns {Object}
     */
    async createModule(name, cb) {
        if (!/\.js$/.test(name)) {
            name += '.js';
        }

        if (!this._modules[name]) {
            const {default: module} = await import(`../../js/page/modules/${name}`);
            this._modules[name] = new module(this);
        }

        if (typeof cb === 'function') {
            cb(this._modules[name]);
            return;
        }

        return this._modules[name];
    }

    /**
     * userId игрока
     * @returns {Number}
     */
    get userId() {
        return this._data.avatarId;
    }

    destroy() {
        _.each(this._modules, (module, name) => {
            module.destroy();
        });

        this._modules = null;
    }
}

export default Controller;
