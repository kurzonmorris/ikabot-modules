import Parent from './barracks.js';
import { parseTimeString, getInt } from '../../utils.js';
import { UnitIds } from '../../const.js';

class Page extends Parent {

    init() {
        this.prepare();
        this.ikariamPremiumToggle(['#premium_btn2']);

        this.updateUnitCount();
        this.checkRecruiting();
    }

    checkRecruiting() {
        let $container = $('#unitConstructionList');

        if (!$container.length) {
            Front.ikaeasyData.getCurrentCity().military.setTraining('ships', []);
            return;
        }

        let units = [];
        let time = parseTimeString($container.find('#buildCountDown').text().trim());

        units.push({
            time: _.now() + time,
            type: 'ships',
            units: this._checkRecruitingWrapper($container)
        });

        _.each($container.find('> .constructionBlock'), ($block) => {
            $block = $($block);
            let time = parseTimeString($block.find('> h4 span').text().trim());

            units.push({
                time: _.now() + time,
                type: 'ships',
                units: this._checkRecruitingWrapper($block)
            });
        });

        Front.ikaeasyData.getCurrentCity().military.setTraining('ships', units);
    }

    _checkRecruitingWrapper($container) {
        let result = {};

        _.each($container.find('> .army_wrapper'), ($w) => {
            $w = $($w);
            let m = $w.find('.army').attr('class').match(/\bs([0-9]+)/);

            if ((m) && (m.length)) {
                let unit = m[1];
                result[UnitIds[unit]] = getInt($w.find('.unitcounttextlabel').text().trim());
            }
        });

        return result;
    }

    updateUnitCount() {
        let military = this._city.military;
        function update(type, cnt) {
            military.setCount(type, getInt(cnt));
        }

        _.each($('#units li.unit'), ($li) => {
            $li = $($li);
            let unit = $li.attr('class').replace('unit', '').trim();
            update(unit, $li.find('.scroll_view').text());
        });
    }

}

export default Page;
