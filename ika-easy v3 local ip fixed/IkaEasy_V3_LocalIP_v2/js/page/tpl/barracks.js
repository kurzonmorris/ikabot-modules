import Parent from './dummy.js';
import { Resources, UnitIds } from '../../const.js';
import { parseTimeString, getInt } from '../../utils.js';

const RESOURCES_LIST = [Resources.CITIZENS, Resources.WOOD, Resources.SULFUR, Resources.WINE, Resources.GLASS];

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle([$('.premiumOffer').closest('.contentBox01h'), '#premium_btn2']);
        this.prepare();

        this.updateUnitCount();
        this.checkRecruiting();
    }

    checkRecruiting() {
        let $container = $('#unitConstructionList');

        if (!$container.length) {
            Front.ikaeasyData.getCurrentCity().military.setTraining('units', []);
            return;
        }

        let units = [];
        let time = parseTimeString($container.find('#buildCountDown').text().trim());

        units.push({
            time: _.now() + time,
            type: 'units',
            units: this._checkRecruitingWrapper($container)
        });

        _.each($container.find('> .constructionBlock'), ($block) => {
            $block = $($block);
            let time = parseTimeString($block.find('> h4 span').text().trim());

            units.push({
                time: _.now() + time,
                type: 'units',
                units: this._checkRecruitingWrapper($block)
            });
        });

        Front.ikaeasyData.getCurrentCity().military.setTraining('units', units);
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

    prepare() {
        if (this.options.get('units_max', true)) {
            this.addMaxValue();

            $('.forminput input.textfield').on('change input', () => {
                this.updateMaxUnits();
            });

            $('.sliderinput').mouseup(() => {
                this.updateMaxUnits();
            });
        }
    }

    _getResourceForUnit(type, $resources) {
        let resource = $resources.find(`li.${type}`);
        if (resource.length) {
            return getInt(resource.text());
        }

        return null;
    }

    addMaxValue() {
        this.cityResources = this._city.resources;
        this.resources = [];

        _.each($('ul#units li.unit'), (el) => {
            let $el = $(el);
            let $resources = $('ul.resources', $el);

            let unit = {};
            _.each(RESOURCES_LIST, (type) => {
                unit[type] = this._getResourceForUnit(type, $resources);
            });

            this.resources.push(unit);
        });

        this.updateMaxUnits();
    }

    updateMaxUnits(){
        let currentUnitsCosts = {
            [Resources.CITIZENS] : 0,
            [Resources.WOOD]     : 0,
            [Resources.SULFUR]   : 0,
            [Resources.WINE]     :  0,
            [Resources.GLASS]    : 0
        };

        let $units = $('ul#units li.unit');
        _.each($units, (el, index) => {
            let cnt = getInt($('div.forminput input', el).val());
            if (!cnt) {
                return;
            }

            _.each(currentUnitsCosts, (v, k) => {
                currentUnitsCosts[k] += cnt * this.resources[index][k];
            });
        });

        _.each($units, (el, index) => {
            let max = 99999999999;
            let cnt = getInt($('div.forminput input', el).val());

            _.each(RESOURCES_LIST, (type) => {
                let unit = this.resources[index];
                if (unit[type] !== null) {
                    let cityRes = this.cityResources[type] - currentUnitsCosts[type] + cnt * unit[type];
                    if (cityRes <= 0) {
                        max = 0;
                    } else {
                        max = Math.min(max, Math.floor(cityRes / unit[type]));
                    }
                }
            });

            let $btn = $('.forminput > .textfieldContainer > a', el);
            if ($btn.length) {
                $btn.addClass('ikaeasy_barracks_max').html(` / ${max}`);
                $btn.off('click').click((e) => {
                    e.preventDefault();

                    if (max > 0) {
                        $('div.forminput input', el).val(max).click();
                        this.updateMaxUnits();
                    }
                });
            }
        });
    }
}

export default Page;
