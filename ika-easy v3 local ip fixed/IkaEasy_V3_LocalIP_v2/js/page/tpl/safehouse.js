import Parent from './dummy.js';
import { getItem, getInt, updateObjectItem, formatNumber } from '../../utils.js';

class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle(['#tabReports .archiveButton .ambrosia']);

        const list = $('table.reportTable.resourcesTable');
        for(let i = 0; i < list.length; i++) {
            const $table = $(list[i]);
            await this.appendTable($table, i);
        }
    }

    async appendTable($table, index) {
        if ($table.data('updated')) {
            return;
        }

        _.each($table.find('tr'), (el, i) => {
            let $el = $(el);
            if (i === 0) {
                $el.append(`<th class="count">${LANGUAGE.getLocalizedString('safehouse_available_steal')}</th>`);
                return;
            }

            $el.addClass('js-resource-row').append(`<td class="count js-resource">0</td>`);
        });

        const tpl = await this.render('safehouse');
        let $tr = $(tpl);
        $table.append($tr);

        let $info = $table.closest('tr.report').prev();
        let targetCityId = $info.find('.targetCity a').attr('href').match(/selectCity=([0-9]+)/)[1];
        let levels = getItem('warehouse-level');
        if ((levels) && (levels[targetCityId] !== undefined)) {
            $tr.find('.js-warehouse').val(levels[targetCityId]);
        }

        $tr.find('.js-warehouse').on('input', () => {
            this.refreshResourcesReports($table);
        });

        $tr.find('.js-idle').on('change', () => {
            this.refreshResourcesReports($table);
        });

        this.refreshResourcesReports($table);
        this.getReportInfo($table);
        $table.data('updated', true);
    }

    getReportInfo($table) {
        // Узнаем инфу о репорте
        let storage = {};
        let $info = $table.closest('tr.report').prev();
        let m = $info.find('.targetCity a').attr('href').match(/xcoord=([0-9]+)&ycoord=([0-9]+)&selectCity=([0-9]+)/);
        if (m && m.length) {
            //onclick="toggleElement('tbl_mail97634'); toggleArrow($('#button97634')[0]);"
            storage.ownerId = parseInt($info.find('.targetOwner').attr('onclick').match(/tbl_mail([0-9]+)/)[1]);
            storage.ownerName = $info.find('.targetOwner').text().trim();
            storage.date = $info.find('.date').text().trim();
            storage.coordX = parseInt(m[1]);
            storage.coordY = parseInt(m[2]);
            storage.cityId = parseInt(m[3]);
            storage.cityName = $info.find('.targetCity a').text().replace(/(\[.*?\])/, '').trim();
            storage.resources = {};

            _.each($table.find('tr.js-resource-row'), (el) => {
                let $el = $(el);
                let res = $el.find('.unitname img').attr('src').match(/icon_([a-z]+)\.png/)[1];
                storage.resources[res] = getInt($el.find('.count:eq(0)').text());
            });
        } else {
            console.error('Cannot parse url', $info.find('.targetCity a').attr('href'))
        }
    }

    refreshResourcesReports($table) {
        let $info = $table.closest('tr.report').prev();
        let targetCityId = $info.find('.targetCity a').attr('href').match(/selectCity=([0-9]+)/)[1];

        let warehouseLevel = getInt($table.find('.js-warehouse').val());
        updateObjectItem('warehouse-level', {targetCityId: warehouseLevel});

        let isIdle = $table.find('.js-idle').prop('checked');

        let sum = 0;
        let protectedResource = (isIdle) ? 15 + warehouseLevel * 80 : 100 + warehouseLevel * 480;
        _.each($table.find('tr.js-resource-row'), (el) => {
            let $el = $(el);
            let cnt = getInt($el.find('.count:eq(0)').text());
            let toStole = Math.max(0, cnt - protectedResource);
            sum += toStole;

            $el.find('.js-resource').html(formatNumber(toStole));
        });

        $table.find('.js-result-1').html(LANGUAGE.getLocalizedString('safehouse_resources', {SUM: formatNumber(sum)}));
        $table.find('.js-result-2').html(LANGUAGE.getLocalizedString('safehouse_ships', {SHIPS: Math.ceil(sum / 500)}));
    }
}

export default Page;
