import Parent from './dummy.js';
import { getInt } from '../../utils.js';

class Page extends Parent {

    init() {
        if ($('#ikaeasy_max_ships').length) {
            return;
        }

        let $el = $('<div class="ikaeasy_max_btn" id="ikaeasy_max_ships"></div>');
        $('#plusminus').append($el);

        $el.click(() => {
            let cnt = parseInt($('#transporterCount').text());
            $('#extraTransporter').val(this._data.ships - cnt);
            $('#totalFreight').text(this._data.ships * 500);
        });

        this.addButtons();
    }

    addButtons() {
        if (!this.options.get('units_to_ship', true)) {
            return;
        }

        if ($('#ikaeasy_attack_btns_div').length) {
            return;
        }

        let $allBtnDiv = $('<div class="ikaeasy_attack_btns_div ikaeasy-fix-button" id="ikaeasy_attack_btns_div"></div>');

        $allBtnDiv.append(this._nothingButton());
        $allBtnDiv.append(this._halfButton());
        $allBtnDiv.append(this._allButton());

        $allBtnDiv.insertBefore('div.newSummary');
    }

    _allButton() {
        let $allBtn = $(`<a class="button">${LANGUAGE.getLocalizedString('attack_btn_all')}</a>`);
        $allBtn.click(() => {
            _.each($('ul.assignUnits li'), (el) => {
                let $el = $(el);
                let count = getInt($('.amount', $el).text());
                $('input.textfield', $el).val(count).click();
            });
        });

        return $allBtn;
    }

    _halfButton() {
        //Half button
        let $halfBtn = $(`<a class="button">${LANGUAGE.getLocalizedString('attack_btn_half')}</a>`);
        $halfBtn.click(() => {
            _.each($('ul.assignUnits li'), (el) => {
                let $el = $(el);
                let count = getInt($('.amount', $el).text());
                $('input.textfield', $el).val(Math.floor(count / 2)).click();
            });
        });

        return $halfBtn;
    }

    _nothingButton() {
        //Half button
        let $nothingBtn = $(`<a class="button">${LANGUAGE.getLocalizedString('attack_btn_nope')}</a>`);
        $nothingBtn.click(() => {
            _.each($('ul.assignUnits li'), (el) => {
                let $el = $(el);
                $('input.textfield', $el).val('0').click();
            });
        });

        return $nothingBtn;
    }

}

export default Page;
