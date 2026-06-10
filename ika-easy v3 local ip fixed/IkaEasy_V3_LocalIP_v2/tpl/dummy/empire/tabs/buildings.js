import Parent from '../../../_dummy.js';
import { Buildings } from '../../../../js/const.js';

class PrepareTpl extends Parent {

    getData() {
        _.each(this._data.cities, (city) => {
            const mcity = this._data.manager.getCity(city.id);
            city._discount = this._helpers.getDiscount(mcity);

            if (!mcity) {
                return;
            }

            mcity._hasConstructingBuilding = mcity.hasConstructingBuilding();

            if (!mcity.buildings) {
                return;
            }
            city._buildings = [];

            _.each(Buildings, (building) => {
                let info = this._data.buildings[building];

                if (!info.cnt) {
                    return;
                }

                city._mcity = mcity.toObject();
                let b = mcity.getBuildingByType(building);

                if (b) {
                    let buildingInfos = this._helpers.getBuildingInfo(mcity, building, city._discount);
                    for (let i = 0; i < info.cnt; i++) {
                        if (buildingInfos[i]) {
                            let buildingInfo = buildingInfos[i];

                            city._buildings.push({
                                type: 'td_content',
                                building: building,
                                b: b,
                                buildingInfo: buildingInfo,
                                city: mcity
                            });
                        } else {
                            city._buildings.push({
                                type: 'td_empty_content'
                            });
                        }
                    }
                } else {
                    for(let i = 0; i < info.cnt; i++) {
                        city._buildings.push({
                            type: 'td_empty_b_content',
                            building: building
                        });
                    }
                }
            });
        });

        return this._data;
    }
}

export default PrepareTpl;
