import Db from '../../helper/db.js';
import IkalogsRu from '../../helper/ikalogsRu.js';
import Parent from './dummy.js';
import { getInt } from '../../utils.js';
import { TradeGoodOrdinals } from '../../const.js';

class Module extends Parent {
    async init() {
        await this.islandsSearch();
    }

    async islandsSearch() {
        if ($('#ikaeasy_islandsSearch').length) {
            return;
        }

        const db = Db.db();
        const tpl = await this.render('worldmap-islandSearch', { db: db, resources: TradeGoodOrdinals });
        $('#footer .footerbg').prepend(tpl);

        this.foundedCoords = [];
        this.foundedCoordsLastLength = null;
        this.searchParameters = { wonder : [], tradegood: [], general: [] };
        this.existsCorrds = {};
        this.activeFoundedCoords = 0;

        console.log(11)
        const islands = await IkalogsRu.getMines();
        console.log(22)
        $('#worldmap .islandTile').toArray().forEach(island => this.drawResources(islands, $(island)));

        $(document).off('.dom').on('DOMSubtreeModified.dom', '#worldmap .islandTile', (ev) => {
            setTimeout(() => {
                if (!$(ev.currentTarget).hasClass('oceanTile') && !$(ev.currentTarget).find('.ikaeasy-resource').length) {
                    this.drawResources(islands, $(ev.currentTarget));
                }

                this.filterIsland($(ev.currentTarget));
                this.setFromCounter();
                this.parent.mapChanged();
            }, 100);
        });

        console.log(1111, $('#ikaeasy_islandsSearch button').length )
        $('#ikaeasy_islandsSearch button').on('click', (e) => {
            this.buttonClick($(e.currentTarget));
        });

        $('#ikaeasy_islandSearch_prev, #ikaeasy_islandSearch_next').click((e) => {
            e.preventDefault();

            let $el = $(e.currentTarget);
            if (!$el.hasClass('unactive')) {
                this.activeFoundedCoords += parseInt($el.data('act'));
                this.changeIsland();
            }
        });
    }

    _checkCondition(coords) {
        if ((this.searchParameters.wonder.length > 0) && (this.searchParameters.wonder.indexOf(coords.wonder) === -1)) {
            return false;
        }

        if ((this.searchParameters.tradegood.length > 0) && (this.searchParameters.tradegood.indexOf(coords.tradegood) === -1)) {
            return false;
        }

        if (this.searchParameters.general.length > 0) {
            if ((this.searchParameters.general.indexOf('generalfull') > -1) && (coords.cities >= 16)) {
                return false;
            }

            if ((this.searchParameters.general.indexOf('generalempty') > -1) && (!coords.cities)) {
                return false;
            }
        }

        return true;
    }

    filterIsland($el, repeat = 0) {
        if ((!$el.hasClass('oceanTile')) && (!$el.hasClass('ikaeasy_islandSearch_filtered'))) {
            let $wonder = $el.find(".wonder");
            let $tradegood = $el.find(".tradegood");
            if ((!$wonder.length || (!$tradegood.length))) {
                return;
            }

            let cntCities = parseInt($el.find(".cities").text());
            if (isNaN(cntCities)) {
                setTimeout(() => {
                    this.filterIsland($el, ++repeat);
                }, 100);
                return;
            }

            let coords = this.getSelectedCoord($el);

            coords.wonder = $wonder.attr('class').match(/(wonder[0-9]+)/)[1];
            coords.tradegood = $tradegood.attr('class').match(/(tradegood[0-9]+)/)[1];
            coords.cities = cntCities;

            let coordsKey = `${coords.x}-${coords.y}`;
            if (this._checkCondition(coords)) {
                $el.addClass('ikaeasy_islandSearch_filtered');

                if (!this.existsCorrds[coordsKey]) {
                    this.foundedCoords.push(coords);
                    this.existsCorrds[coordsKey] = true;
                }
            } else {
                if (this.existsCorrds[coordsKey]) {
                    this.foundedCoords = _.filter(this.foundedCoords, (el) => {
                        return ((el.x !== coords.x) || (el.y !== coords.y)) && this._checkCondition(el);
                    });

                    this.existsCorrds[coordsKey] = false;
                }

                if (this.foundedCoords.length) {
                    $('#ikaeasy_islandSearch_next').removeClass('unactive');
                }
            }
        }
    }

    buttonClick($el) {
        let $worldmap = $('#worldmap');
        let $islands = $worldmap.find('.islandTile');

        let name = $el.data('name');
        let type = $el.data('type');

        $el.toggleClass("active");

        // кнопки показа ресурсов на карте
        if ($el.closest('.ikaeasy_island_show_resources').length) {
            const resources = $islands.find(`.ikaeasy-resource-${name}`);
            $el.is('.active') ? resources.removeClass('ikaeasy-d-n') : resources.addClass('ikaeasy-d-n');

            return;
        }

        $worldmap.addClass('ikaeasy_islandSearching');
        $('#ikaeasy_islandSearch_next').removeClass('unactive');

        if ($el.hasClass("active")) {
            this.searchParameters[type].push(type + name);
        } else {
            this.searchParameters[type] = _.without(this.searchParameters[type], type + name)
        }

        if ((!this.searchParameters.wonder.length) && ((!this.searchParameters.tradegood.length)) && ((!this.searchParameters.general.length))) {
            this.reset();
        } else {
            $islands.removeClass('ikaeasy_islandSearch_filtered').each((k, el) => {
                this.filterIsland($(el));
            });

            this.setFromCounter();
        }

        if (name === 'reset') {
            this.reset();
        }
    }

    reset() {
        $('#ikaeasy_islandsSearch div:not(.ikaeasy_island_show_resources) button').removeClass('active');
        this.setFromCounter(0);
        this.searchParameters = { wonder : [], tradegood: [], general: [] };
        this.foundedCoords = [];

        this.existsCorrds = {};
        this.activeFoundedCoords = 0;

        $('#worldmap .ikaeasy_islandSearch_filtered').removeClass('ikaeasy_islandSearch_filtered');
        $('#worldmap').removeClass('ikaeasy_islandSearching');
        $('#ikaeasy_islandSearch_prev, #ikaeasy_islandSearch_next').addClass('unactive');
    }

    setFromCounter(count){
        if (typeof count === 'undefined') {
            count = this.foundedCoords.length;
        }

        if (this.foundedCoordsLastLength !== count) {
            $('#ikaeasy_islandSearch_counter .ikaeasy_iSS_cnt_from').text(count);
            this.foundedCoordsLastLength = count;
        }

        if (!this.foundedCoords.length) {
            $('#ikaeasy_islandSearch_prev, #ikaeasy_islandSearch_next').addClass('unactive');
        }
    }

    changeIsland() {
        if (!this.foundedCoords.length) {
            return;
        }

        if (!this.foundedCoords[this.activeFoundedCoords]) {
            this.activeFoundedCoords = 0;
        }

        this.jumpToCoord( this.foundedCoords[this.activeFoundedCoords].x, this.foundedCoords[this.activeFoundedCoords].y );

        $('#ikaeasy_islandSearch_counter .ikaeasy_iSS_cnt_current').text(this.activeFoundedCoords);
        $('#ikaeasy_islandSearch_next').toggleClass('unactive', this.activeFoundedCoords >= this.foundedCoords.length);
        $('#ikaeasy_islandSearch_prev').toggleClass('unactive', !this.activeFoundedCoords);
    }

    jumpToCoord(x, y){
        $('#inputXCoord').val(x);
        $('#inputYCoord').val(y);
        $('form[name="navInputForm"] .submitButton').click();

        // ToDo проверить работает ли без этой функции... теоретически должно
        //this.parent.showTravelTime();
    }

    getSelectedCoord($el){
        let res = $el.attr('title').split(' ');
        if (typeof res !== "undefined") {
            res = res[1].split(':');

            return {
                x: getInt(res[0]),
                y: getInt(res[1])
            };
        }

        return false;
    }

    drawResources(islands, $island) {
        const [name] = $island.attr('title').split(' ');

        const [wood, mine, wonder] = islands[name] || ['-', '-', '-'];

        const $mine = $island.find('.tradegood');
        const $wonder = $island.find('.wonder');

        $mine
            .append(`<span class="ikaeasy-resource ikaeasy-resource-mine ikaeasy-l--20 ikaeasy-d-n">${mine}</span>`);

        $wonder
            .append(`<span class="ikaeasy-resource ikaeasy-resource-wonder ikaeasy-t-20 ikaeasy-l--20 ikaeasy-d-n">${wonder}</span>`);

        $island
            .append(`<div class="ikaeasy-resource ikaeasy-resource-wood ikaeasy-b-70 ikaeasy-l-55 ikaeasy-d-n"><div class="ikaeasy-resource-icon ikaeasy-w-24 ikaeasy-h-20" style="background-image: url(/cdn/all/both/resources/icon_wood.png);"></div><span>${wood}</span></div>`)
    }
}

export default Module;
