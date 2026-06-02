import ControllerBase from '../../controller.js';import { getInt, getFloat } from '../../../utils.js';

class townHallController extends ControllerBase{
    init(){
        const cityId = getInt(this.DOMselect('.content input[name="cityId"]').val());

        if(cityId !== 0){
            this.setCurrentCity(cityId);
        }//else _city will bring current view city

        const priests = getInt(this.DOMselect('#js_TownHallPopulationGraphPriestCount').text());
        const culturalGoods = getInt(this.DOMselect('#js_TownHallSatisfactionOverviewCultureBoniTreatyBonusValue').text()) / 50;
        const scientists = getInt(this.DOMselect('#js_TownHallPopulationGraphScientistCount').text());
        const happinessLargeValue = getInt(this.DOMselect('#js_TownHallHappinessLargeValue').text());
        const populationGrowthValue = getFloat(this.DOMselect('#js_TownHallPopulationGrowthValue').text());
        const occupiedSpace = getInt(this.DOMselect('#js_TownHallOccupiedSpace').text());
        const maxInhabitants = getInt(this.DOMselect('#js_TownHallMaxInhabitants').text());

        this._city.set('priests', priests);
        this._city.set('culturalGoods', culturalGoods);
        this._city.set('scientists', scientists);
        this._city.set('happinessLargeValue', happinessLargeValue); // общее число счастья
        this._city.set('populationGrowthValue', populationGrowthValue); // рост в час
        this._city.set('occupiedSpace', occupiedSpace); // жилая площадь, тек.
        this._city.set('maxInhabitants', maxInhabitants); // жилая площадь, всего
    }
}
export default new townHallController();
