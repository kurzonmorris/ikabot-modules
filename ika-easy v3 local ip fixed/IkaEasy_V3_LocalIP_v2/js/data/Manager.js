import Events from '../helper/event.js';
import Storage from '../helper/storage.js';
import options from '../helper/options.js';
import HttpClient from '../helper/httpClient.js';
import EntityCity from '../data/city.js';
import EntityMovement from '../data/movement.js';
import Info from '../data/info.js';
import Research from '../data/research.js';
import Premium from '../data/premium.js';
import { Movements } from '../const.js';
import { getServerDomain, getServerWorld } from '../utils.js';

const VERSION = 5;
const GLOBAL_KEY = 'ikaeasy';

class Manager extends Events {
    constructor(callback) {
        super();

        let cb = () => {
            if (_.isFunction(callback)) {
                callback();
            }
        };

        this.loaded = false;
        this.cities = {};
        this.citiesStorage = new Map();
        this.movements = [];
        this.info = Info;
        this.research = Research;
        this.options = {};
        this.premium = Premium;
        this.version = 0;

        Storage.get(GLOBAL_KEY).then(data => {
            this.updateAll(data || {}, () => {
                if (this.version < VERSION) {
                    this._updateToCurrentVersion(cb);
                    this.version = VERSION;
                } else {
                    cb();
                }
            });
        });

        Storage.on(GLOBAL_KEY, (val) => {
            this.updateAll(val.newValue || {});
        });
    }

    _updateToCurrentVersion(callback) {
        if (VERSION === 4) {
            this.research.forceUpdate(callback);
        }

        if (VERSION === 5) {
            if (this.options.notification_wait_interaction) {
                options.set('notification_wait_no_interaction', !this.options.notification_wait_interaction);
                delete this.options.notification_wait_interaction;
            }
        }
    }

    updateAll(data, callback) {
        this.cities = {};
        this.citiesStorage = new Map();
        this.movements = [];
        this.options = data.options || {};
        this.version = data.version || 0;
        this.info._data = data.info || this.info._data;

        _.each(data.cities, (data, cityId) => {
            let city = new EntityCity(cityId, this);
            city.load(data);

            this.cities[cityId] = city;
            this.citiesStorage.set(cityId, city);
        });

        this.research.updateData(data.research);
        this.premium.updateData(data.premiumFeatures);

        _.each(data.movements, (data) => {
            let m = new EntityMovement(data, true);
            this.movements.push(m);
        });

        if (!this.loaded) {
            this.getCurrentCity().update();
            this.loaded = true;

            if (_.isFunction(callback)) {
                callback();
            }
        }

        setTimeout(() => {
            this.emit('update');
        }, 1);
    }

    getResearch() {
        return this.research;
    }

    getCity(id) {
        id = parseInt(id);

        if (this.cities[id]) {
            return this.cities[id];
        }


        let c = new EntityCity(id, this);
        this.cities[id] = c;
        this.citiesStorage.set(id, c);
        c.update();

        return c;
    }

    get currentCityId(){
        return Front.data.city && Front.data.city.id ? parseInt(Front.data.city.id) : Front.data.cities.selectedCityId;

    }

    getCurrentCity() {
        return this.getCity(this.currentCityId);
    }

    save() {
        if (this._timeoutCity) {
            return;
        }

        this._timeoutCity = setTimeout(() => {
            this._timeoutCity = null;
            Storage.set(GLOBAL_KEY, this.getJson());
        }, 10);
    }



    // MOVEMENTS
    addMovement(movement) {
        this.movements.push(movement);
        this.save();
    }

    removeMovement(movementId) {
        let index = _.findIndex(this.movements, {id: movementId});
        if (index === -1) {
            return;
        }

        let movement = this.movements[index];
        let stage = movement.getStage();

        if ((stage) && (stage.stage === Movements.Stage.LOADING)) {
            // Надо исправить время всех последующих маршрутов из этого города
            let freeTime = stage.finishTime - _.now();
            _.each(this.movements, (m) => {
                if ((m.id !== movementId) && (m.originCityId === movement.originCityId)) {
                    let s = m.getStage();
                     if ((s) && (s.stage === Movements.Stage.LOADING) && (s.startTime > stage.startTime)) {
                        m.startTime -= freeTime;
                    }
                }
            });
        }

        this.movements.splice(index, 1);
        this.save();
    }




    getJson() {
        let cities = {};
        _.each(this.cities, (city, cityId) => {
            city = city.toSave();
            if (city) {
                cities[cityId] = city;
            }
        });

        let movements = [];
        _.each(this.movements, (v) => {
            if (v.getStage() !== null) {
                movements.push(v.toSave());
            }
        });

        return {
            server: {
                avatarId: Front.data.avatarId,
                domain: getServerDomain(),
                world: getServerWorld(),
                name: Front.data.serverName,
                lang: currentLanguage
            },

            cities: cities,
            research: this.research.toJSON(),
            info: this.info._data,
            options: this.options || {},
            premiumFeatures: this.premium.toJSON(),
            movements: movements,
            version: VERSION
        };
    }

    getOwnCities() {
        return [...this.citiesStorage.keys()]
            .filter(city => this.citiesStorage.get(city)._data.isOwn === true)
            .sort((x,y) => x == this.currentCityId ? 0 : y == this.currentCityId ? -1 : 0)
    }

    get ownCities(){
        let cities = [];
        for(const _cityId of this.getOwnCities()){
            cities.push(new EntityCity(_cityId, this));
        }
        return cities;
    }

    getCapitalCity(){
        return this.ownCities.filter(city => city.isCapital === true)[0];
    }

    getBuildingInfo(building) {
        let data = {
            cnt: 0
        };

        _.each(this.cities, (city, cityId) => {
            if ((city.isOwn) && (city.buildings) && (city.buildings[building]) && (city.buildings[building].length)) {
                data.cnt = Math.max(data.cnt, city.buildings[building].length);
                data.name = city.buildings[building][0].name;
            }
        });

        return data;
    }

    async ajaxUpdateAllCities(){
        for(const city of this.ownCities){
            await city.ajaxUpdate();
        }
    }
    async ajaxUpdatePalace(){
        const capitalCity = this.getCapitalCity();
        if(capitalCity.buildings.palace && capitalCity.buildings.palace[0]){
            await HttpClient.ikariam('/', {
                view: 'palace',
                cityId: capitalCity.cityId,
                position: capitalCity.buildings.palace[0].position,
            });
        }
    }

    isPremiumFeatureEnabled(feature) {
        return this.premium._data[feature] && this.premium._data[feature].activeUntil;
    }
}

export default Manager;
