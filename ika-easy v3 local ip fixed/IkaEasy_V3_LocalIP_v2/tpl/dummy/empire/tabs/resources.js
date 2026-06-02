import Parent from '../../../_dummy.js';

class PrepareTpl extends Parent {
    constructor(data) {
        super(data);
    }

    getData() {
        this._data.manager.info._government = this._data.manager.info.get('government');

        let total = {population: 0, corruption: 0, research: 0, growth: 0, max: 0, resource: {}};

        _.each(this._data.cities, (city) => {
            let mcity = this._data.manager.getCity(city.id);

            city._updatingTime = mcity.updatingTime;
            city._popData = mcity.getPopulationData();
            city._resInfo = mcity.getResourcesInfo();

            total.corruption += Math.floor(mcity.getCorruption() * 100);
            total.research   += Math.floor(mcity.getResearch());

            if (city._popData) {
                total.population += Math.floor(city._popData.population);
                total.growth     += parseFloat(city._popData.growth.toFixed(2));
                total.max        += city._popData.max;
            }


            city._mcity = mcity.toObject();
            if (mcity.buildings) {
                city._mcity._corruption = mcity.getCorruption();
                city._mcity._scientists = mcity.getScientists();
                city._mcity._maxScientists = mcity.getMaxScientists();
                city._mcity._scientistsPercent = mcity.getScientistsPercent();
            }
        });

        this._data._total = total;
        return this._data;
    }
}

export default PrepareTpl;
