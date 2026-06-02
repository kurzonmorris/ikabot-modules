import IkalogsRu from '../../helper/ikalogsRu.js';
import Db from '../../helper/db.js';
import Parent from './dummy.js';
import {secondToTime, getInt} from '../../utils.js';

const ISLANDS = Db.getIslands();

class World extends Parent {
    init() {
        if (this.options.get('world_search_island', true)) {
            this.createModule('worldmap-islandSearch', (module) => {
                this.searchIslands = module;

                this.sentEmptyIslands = {};
                this.islandsTimeTravel();
                this.updateIkalogsMap();
            });
        } else {
            this.sentEmptyIslands = {};
            this.islandsTimeTravel();
            this.updateIkalogsMap();
        }
    }

    mapChanged() {
        this.updateIkalogsMap();
    }

    updateIkalogsMap() {

        let cities = $('#map1 .islandTile .cities');
        let emptyIslands = [];
        _.each(cities, ($el) => {
            $el = $($el);
            let cnt = parseInt($el.text().trim());
            if ((!isNaN(cnt)) && (cnt === 0)) {
                let m = $el.parent().attr('title').trim().match(/\[([0-9]+):([0-9]+)\]/);
                if ((m) && (m.length)) {
                    let islandId = ISLANDS[m[1]][m[2]][0];

                    if (!this.sentEmptyIslands[islandId]) {
                        emptyIslands.push(islandId);
                        this.sentEmptyIslands[islandId] = true;
                    }
                }
            }
        });

        if (emptyIslands.length) {
            IkalogsRu.sendWorldEmptyIslands(emptyIslands);
        }
    }


    islandsTimeTravel() {
        $('.islandTile, #mapCoordInput .submitButton').on('click', () => {
            this.showTravelTime();
        });

        this.showTravelTime();
    }

    showTravelTime(){
        let $el = $('#ikaeasy_islands_travel_time');

        if (!$el.length) {
            $el = $('<div id="ikaeasy_islands_travel_time"></div>');
            $('#mapCoordInput').prepend($el);
        }

        setTimeout(() => {
            let activeIsland = this.getActiveIsland();
            let targetIsland = this.getWorldActiveIsland();

            if ((targetIsland.x === activeIsland.x) && (targetIsland.y === activeIsland.y)) {
                $el.html('00:10');
                return;
            }

            let x = targetIsland.x - activeIsland.x;
            let y = targetIsland.y - activeIsland.y;
            let math = 1200 * Math.sqrt(Math.pow(x, 2) + Math.pow(y, 2));

            $el.html(secondToTime(math));
        }, 400);
    }

    getActiveIsland(){
        let res = $('#js_citySelectContainer a').text().split(' ');

        if (typeof res !== 'undefined') {
            res = res[0].split(':');

            return {
                x: getInt(res[0]),
                y: getInt(res[1])
            };
        }

        return null;
    }

    getWorldActiveIsland(){
        return {
            x: getInt($('#inputXCoord').val()),
            y: getInt($('#inputYCoord').val())
        };
    }

}

export default World;
