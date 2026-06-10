import HttpClient from '../helper/httpClient.js';
import Military from '../data/military.js';
import {
    Buildings,
    BuildingsMultiple,
    CityType,
    Government,
    MAX_SCIENTISTS,
    PORT_LOADING_SPEED,
    PremiumFeatures,
    Research,
    Resources,
    TradeGoodOrdinals,
    WINE_USE,
} from '../const.js';

const VERSION = 2;
let instances = {};

class City {
    constructor(cityId, manager) {
        if(cityId in instances){//singleton per cityId
            return instances[cityId];
        }
        this._manager = manager;
        this.cityId = parseInt(cityId);
        this.military = new Military(this);

        this._data = {
            id: cityId,
            resources: {},
            maxResources: {},
            production: {},

            culturalGoods: 0,
            priests: 0,
            tavernWineLevel: 0,
            scientists: 0,

            happinessLargeValue: 0,
            populationGrowthValue: 0,
            occupiedSpace: 0,
            maxInhabitants: 0
        };
        instances[cityId] = this;
    }

    toObject() {
        return _.cloneDeep(this._data);
    }

    async ajaxUpdate(){
        await HttpClient.ikariam('/', {
            view: 'townHall',
            cityId: this.cityId,
            position: 0,
            backgroundView: 'city',
            currentCityId: this.cityId
        });
    }

    load(data) {
        if ((!data.city) || (!data.military)) {
            return;
        }

        this._data = data.city;
        this.military.load(data.military);

        if (this._data.__version < VERSION) {
            this._updateToCurrentVersion();
            this._data.__version = VERSION;
        }
    }

    get(key) {
        return this._data[key];
    }

    set(key, value) {
        if (['id', 'resources', 'maxResources', 'production'].indexOf(key) > -1) {
            return;
        }

        if (this._data[key] !== value) {
            console.debug(`[IkaEasy] City #${this.cityId} set ${key} from ${this._data[key]} to ${value}`);
            this._data[key] = value;
            this._manager.save();
        }
    }

    _updateWineSpending(wineSpendings) {
        if (typeof wineSpendings !== "number") {
            wineSpendings = Front.data.wineSpendings;
        }

        this._data.tavernWineLevel = WINE_USE.indexOf(wineSpendings);

        let vineyard = this.getBuildingByType(Buildings.WINE_PRESS);
        if (vineyard) {
            if (this._data.tavernWineLevel > -1) {
                wineSpendings -= Math.floor(wineSpendings * 0.3);
            } else {
                let t = Math.floor(wineSpendings / 0.7) - 1;
                _.each(WINE_USE, (u, i) => {
                    if (u >= t) {
                        this._data.tavernWineLevel = i;
                        return false;
                    }
                });
            }
        }

        this._data.production[Resources.WINE_SPENDING] = wineSpendings;
    }
    get isCapital(){
        return this._data.isCapital;
    }

    update() {
        if (Front.data.city) {
            this._data.isCapital = Front.data.city.isCapital;
            this._data.islandId  = Front.data.city.islandId;
            this._data.buildings = this._getBuilds();
            this._data.name = Front.data.city.name;
            this._data.ownerId = parseInt(Front.data.city.ownerId);
            this._data.ownerName = Front.data.city.ownerName;
        } else if (Front.data.cities[`city_${this.cityId}`]) {
            this._data.name = Front.data.cities[`city_${this.cityId}`].name;
        }

        this._data.maxActionPoints = Front.data.maxActionPoints;

        let islandResource = _.findKey(TradeGoodOrdinals, (v) => { return v === parseInt(Front.data.producedTradegood); });

        this._data.maxResources[Resources.WOOD]   = Front.data.maxResources.resource;
        this._data.maxResources[Resources.WINE]   = Front.data.maxResources[TradeGoodOrdinals.WINE];
        this._data.maxResources[Resources.MARBLE] = Front.data.maxResources[TradeGoodOrdinals.MARBLE];
        this._data.maxResources[Resources.SULFUR] = Front.data.maxResources[TradeGoodOrdinals.SULFUR];
        this._data.maxResources[Resources.GLASS]  = Front.data.maxResources[TradeGoodOrdinals.GLASS];

        this._data.resources[Resources.WOOD]       = Front.data.resources.resource;
        this._data.resources[Resources.WINE]       = Front.data.resources[TradeGoodOrdinals.WINE];
        this._data.resources[Resources.MARBLE]     = Front.data.resources[TradeGoodOrdinals.MARBLE];
        this._data.resources[Resources.SULFUR]     = Front.data.resources[TradeGoodOrdinals.SULFUR];
        this._data.resources[Resources.GLASS]      = Front.data.resources[TradeGoodOrdinals.GLASS];
        this._data.resources[Resources.CITIZENS]   = Front.data.resources[Resources.CITIZENS];
        this._data.resources[Resources.POPULATION] = Front.data.resources[Resources.POPULATION];

        this._data.production = {}; // Это мы зачищаем, потому что возможен перенос города с одного острова на другой
        this._data.production[Resources.WOOD] = Math.floor(Front.data.resourceProduction * 3600);
        this._data.production[Resources[islandResource]] = Math.floor(Front.data.tradegoodProduction * 3600);
        this._data.production[Resources.WINE_SPENDING] = Front.data.wineSpendings;

        this._data.productionEQ = {};
        this._data.productionEQ[Resources.WOOD] = Front.data.resourceProduction;
        this._data.productionEQ[Resources[islandResource]] = Front.data.tradegoodProduction;

        this._updateWineSpending();

        if (typeof this._data.isOwn === 'undefined') {
            this._data.isOwn = this._forceIsOwn();
        }

        this._data.updatingTime = _.now();

        this._data.__version = VERSION;
        this.save();
    }

    _updateToCurrentVersion() {
        if (VERSION === 2) {
            this._data.isOwn = this._forceIsOwn();
        }
    }

    _forceIsOwn() {
        let city = Front.data.cities[`city_${this._data.id}`];
        if (!city) {
            return false;
        }

        return city.relationship === CityType.OWN;
    }

    /**
     * Get all buildings of this city
     */
    get buildings() {
        return this._data.buildings || null;
    }

    get population() {
        return this._data.resources[Resources.POPULATION];
    }

    _getBuilds(position) {
        if ((!Array.isArray(position)) && (!Front.data.city)) {
            return null;
        }

        if (!Array.isArray(position)) {
            position = Front.data.city.position;
        }

        let builds = {};
        _.each(position, (b, key) => {
            if (!b.name) {
                return;
            }

            b.building = b.building.replace('constructionSite', '').trim();

            if (!builds[b.building]) {
                builds[b.building] = [];
            }

            let building = {
                position:   key,
                building:   b.building,
                canUpgrade: b.canUpgrade,
                isBusy:     b.isBusy,
                isMaxLevel: b.isMaxLevel,
                level:      parseInt(b.level) || 0,
                name:       b.name,
                completed:  (b.completed) ? parseInt(b.completed) : null
            };

            builds[b.building].push(building);
        });

        return builds;
    }

    hasConstructingBuilding() {
        if (this.buildings) {
            return _.some(this.buildings, (b) => {
                return _.some(b, (bb) => {
                    return ((bb.completed) && (bb.completed * 1000 > _.now()));
                });
            });
        }

        return false;
    }

    getPopulationData() {
        if (!this._data.buildings) {
            return null;
        }

        let growth = this._data.populationGrowthValue;
        let happinessClass = '';
        if (growth >= 6) {
            happinessClass = 'ecstatic';
        } else if (growth >= 1) {
            happinessClass = 'happy';
        } else if (growth >= 0) {
            happinessClass = 'neutral';
        } else if (growth >= -1) {
            happinessClass = 'sad';
        } else {
            happinessClass = 'outraged';
        }

        return {
            population: this._data.occupiedSpace,
            max: this._data.maxInhabitants,
            percent: this._data.occupiedSpace * 100 / this._data.maxInhabitants,
            happiness: this._data.happinessLargeValue,
            happinessClass: happinessClass,
            growth: growth
        };
    }

    getCorruption() {
        let palace = this.getBuildingByType(Buildings.GOVERNORS_RESIDENCE) || this.getBuildingByType(Buildings.PALACE);
        let level = palace ? palace.level : 0;
        let corruption = 1 - (level + 1) / this._manager.getOwnCities().length;

        let government = this._manager.info.get('government');
        if (government === Government.ARISTOCRACY ||
            government === Government.OLIGARCHY) {
            corruption += 0.03;
        } else if (government === Government.NOMOCRACY) {
            corruption -= 0.05;
        } else if (government === Government.ANARCHY) {
            corruption += 0.25;
        }

        return Math.min(Math.max(corruption, 0), 1);
    }

    getResearchMultiplier() {
        let research = this._manager.getResearch();
        let rMultiplier = 1.0;
        rMultiplier += research.has(Research.Science.PAPER) ? .02 : 0;
        rMultiplier += research.has(Research.Science.INK) ? .04 : 0;
        rMultiplier += research.has(Research.Science.MECHANICAL_PEN) ? .08 : 0;
        rMultiplier += (research.getLevel(Research.Science.SCIENTIFIC_FUTURE) || 0) * .02;

        return rMultiplier;
    }

    getResearchGovernmentMultiplier() {
        let government = this._manager.info.get('government');

        switch (government) {
            case Government.TECHNOCRACY:
                return 0.05;
            case Government.THEOCRACY:
                return -0.05;

            default:
                return 0;
        }
    }

    getResearch() {
        let cMultiplier = 1.0 - this.getCorruption();
        let rMultiplier = this.getResearchMultiplier() + this.getResearchGovernmentMultiplier();

        return this._data.scientists * rMultiplier * cMultiplier;
    }
    getScientists(){
        return this._data.scientists;
    }
    getMaxScientists() {
        let b = this.getBuildingByType(Buildings.ACADEMY);
        if (!b) {
            return 0;
        }

        return MAX_SCIENTISTS[b.level];
    }

    getScientistsPercent() {
        let max = this.getMaxScientists();
        if (max === 0) {
            return 0;
        }

        return Math.min(this._data.scientists / max * 100, 100);
    }

    getTimeSinceResourceUpdate() {
        return _.now() - this._data.updatingTime;
    }

    getBuildingByType(type) {
        if (!this._data.buildings) {
            return null;
        }

        let b = this._data.buildings[type];
        if (!b) {
            return null;
        }

        return (BuildingsMultiple[type]) ? b : b[0];
    }

    getResourcesInfo() {
        let safe = 100;

        let warehouses = this.getBuildingByType(Buildings.WAREHOUSE);
        _.each(warehouses, (w) => {
            safe  += 480 * w.level;
        });

        if (this._manager.isPremiumFeatureEnabled(PremiumFeatures.DOUBLED_SAFE_CAPACITY)) {
            safe *= 2;
        }

        return {
            safe: safe,
            capacity: this._data.maxResources[Resources.WOOD]
        };
    }


    get productionEQ() {
        if (!this._data.productionEQ) {
            this._data.productionEQ = {};
            _.each(this._data.production, (v, k) => {
                if (k !== Resources.WINE_SPENDING) {
                    this._data.productionEQ[k] = v / 3600;
                }
            });
        }

        return this._data.productionEQ;
    }

    /**
     * Get resources of the city
     * @returns {Object}
     */
    get resources() {
        let diff = Math.floor((_.now() - this._data.updatingTime) / 1000);

        // Обновляем значени ресурсов в зависимости от сохраненной скорости производства
        let resources = _.clone(this._data.resources);


        _.each(this.productionEQ, (v, k) => {
            resources[k] += Math.floor(v * diff);
        });

        resources[Resources.WINE] -= Math.floor(this._data.production[Resources.WINE_SPENDING] / 3600 * diff);
        return resources;
    }

    get id() {
        return this._data.id;
    }

    get updatingTime() {
        let diff = Math.floor((_.now() - this._data.updatingTime) / 1000);
        return {hours: Math.floor(diff / 3600), seconds: diff, time: this._data.updatingTime};
    }

    /**
     * Get city production of resources
     * @returns {Object}
     */
    get production() {
        return this._data.production;
    }

    /**
     * Is own city?
     * @returns {Boolean}
     */
    get isOwn() {
        return this._data.isOwn;
    }

    get name() {
        return this._data.name;
    }

    /**
     * Get city relationship
     * @returns {CityType}
     */
    getRelationship() {
        if (Front.data.cities[`city_${this.cityId}`]) {
            return Front.data.cities[`city_${this.cityId}`].relationship;
        } else {
            return CityType.FOREIGN;
        }
    }

    getLoadingSpeed() {
        let speed = 10;
        let ports = this.getBuildingByType(Buildings.TRADING_PORT);

        if (ports[0]) {
            speed = PORT_LOADING_SPEED[ports[0].level];
        }

        if (ports[1]) {
            speed += PORT_LOADING_SPEED[ports[1].level];
        }

        return speed / Time.SECONDS_PER_MINUTE;
    }

    getBuildingsCostDiscount() {
        // Обновляем список "скидок"
        let building = {wood: 'carpentering', marble: 'architect', wine: 'vineyard', glass: 'optician', sulfur: 'fireworker'};
        let discount = {};

        _.each(building, (v, k) => {
            let b = this.getBuildingByType(v);
            discount[k] = (b) ? b.level : 0;
        });

        let r = this._manager.research;
        let redAll = 0;
        if (r.has(Research.Economy.PULLEY))       { redAll += 2; }
        if (r.has(Research.Economy.GEOMETRY))     { redAll += 4; }
        if (r.has(Research.Economy.SPIRIT_LEVEL)) { redAll += 8; }

        _.each(discount, (v, k) => {
            discount[k] = 1 - ((redAll + v) / 100);
        });

        return discount;
    }

    toSave() {
        if (this.getRelationship() !== CityType.FOREIGN) {
            return {
                city: this._data,
                military: this.military.toSave()
            };
        }

        return null;
    }

    save() {
        this._manager.save();
    }


}

export default City;
