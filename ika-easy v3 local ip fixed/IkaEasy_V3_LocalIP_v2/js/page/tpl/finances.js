import Parent from './dummy.js';
import { execute_js, getInt, formatNumber } from '../../utils.js';
import { Research } from '../../const.js';

class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle(['#js_BadTaxAccountantOffer']);
        await this.gold();
        this.updateScientists();
    }

    updateScientists() {
        let scientistCost = 6;
        if (this._ieData.research.has(Research.Science.LETTER_CHUTE)) {
            scientistCost = 3;
        }

        let cities = this._ieData.getOwnCities();
        $('#finances .table01:eq(1) tr').slice(1, -1).each((index, row) => {
            let $tds = $(row).children('td');
            let city = this._ieData.getCity(cities[index]);
            if ($tds.eq(0).text().trim() === city.name) {
                city.set('scientists', Math.round(-getInt($tds.eq(2).text().replace(',', '')) / scientistCost));
            }
        });
    }

    async gold() {
        let $input = $('#ikaeasy_gold_calc');
        if ($input.length > 0) {
            return;
        }

        let goldPerHour = parseInt(this._data.gold.income + this._data.gold.scientistsUpkeep + this._data.gold.upkeep + this._data.gold.badTaxAccountant);
        const template = await this.render('finances-gold', { gold: goldPerHour });
        $('#finances table.upkeepReductionTable:last').append(template);

        $input = $('#ikaeasy_gold_calc');
        $input.on('input', () => {
            let hours = parseInt($input.val());
            if (hours) {
                let calc = Math.abs(hours) * goldPerHour;
                $('#ikaeasy_gold_custom').html(formatNumber(calc));
            } else {
                $('#ikaeasy_gold_custom').html('&mdash;');
            }
        });

        execute_js('ikariam.templateView.mainbox.scrollbar.adjustSize();');
    }
}

export default Page;
