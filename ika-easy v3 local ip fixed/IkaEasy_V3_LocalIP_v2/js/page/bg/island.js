import Marker from '../../helper/marker.js';
import IkalogsRu from '../../helper/ikalogsRu.js';
import Parent from './dummy.js';
import { GamePlay } from '../../const.js';
import { getItem, setItem, getServerDomain, getServerWorld, numberToBeauty } from '../../utils.js';


class Island extends Parent {
    init() {
        this.island = this._data.island;
        this.islandId = parseInt(this.island.islandId);

        this.showIslandInfo();
        this.colorizeIt('ally');
        this.colorizeIt();

        if (this.options.get('island_details', true)) {
            $('#islandresource').append($(`<div class="ikaeasy_watcher build_blue ikaeasy_watcher_island ikaeasy_watcher-wood"><div class="ikaeasy_watcher_circle">${this.island.wood}</div></div>`));
            $('#islandtradegood').append($(`<div class="ikaeasy_watcher build_blue ikaeasy_watcher_island ikaeasy_watcher-tradegood"><div class="ikaeasy_watcher_circle">${this.island.tradegood}</div></div>`));
            $('#islandwonder').append($(`<div class="ikaeasy_watcher build_blue ikaeasy_watcher_island ikaeasy_watcher-wonder"><div class="ikaeasy_watcher_circle">${this.island.wonder}</div></div>`));
        }
    }

    updated() {
        this.showIslandInfo(false);
        this.colorizeIt('ally');
        this.colorizeIt();
    }

    colorizeIt(colorKind = 'player', removedMarker = false) {

        const resetMarkers = (city, colorKind = null) => {
            let setColor = null;
            // Keep other class if exist
            if (colorKind) {
                const cityHover = city.find('.hover');
                const attributes = cityHover.attr('class');

                let re = /(linkHover_)+[a-z]*_/gmi;
                if (colorKind === 'player') {
                    re = /(linkHover_)+[a-z]*($|\s)/gmi;
                }
                setColor = attributes.match(re);

                if (setColor) {
                    setColor = setColor[setColor.length - 1]
                        .replace('linkHover_', '')
                        .replace(/[^a-z]/gmi, "");
                }
            }

            city.find('.hover').attr('class', 'hover island_feature_img invisible');
            city.find('.link_img').attr('class', 'link_img island_feature_img');
            city.find('.link_img .flag').attr('class', 'position flag animation_16steps');

            if (setColor) {
                setClasses(city, setColor, colorKind === 'ally' ? 'player' : 'ally');
            }
        };

        const removeMarker = (colorKind) => {
            const name = colorKind === 'ally' ? 'ownerAllyTag' : 'ownerName';
            for(let i = 0; i < GamePlay.TOWN_SPOTS; i++) {
                let $city = $(`#cityLocation${i}`);

                if (!$city.hasClass('treaty')) {
                    let info = this.island.cities[i];

                    if ((!info[name]) || (info.type !== 'city')) {
                        continue;
                    }
                    if (this.island.cities[i][name] === removedMarker) {
                        const id = this.island.cities[i].id;
                        let cityToClear;

                        if ($(`.location_list a[href*="${id}"]`).length) {
                            cityToClear = $(`.location_list a[href*="${id}"]`).parent();
                        } else if ($(`.location_list div[saved-href*="${id}"]`).length) {
                            cityToClear = $(`.location_list div[saved-href*="${id}"]`);
                        }

                        resetMarkers(cityToClear, colorKind);
                    }
                }
            }
        };

        const setClasses = ($city, color, colorKind) => {
            resetMarkers($city, colorKind);

            if (color) {
                let isPlayer = colorKind === 'player' ? '_u' : '';
                const isAnimated = $city.hasClass('animated_off') ? '_animated' : '';
                const link = `ikaeasy_city_link_${color + isAnimated + isPlayer}`;
                const hoverLink = `ikaeasy_city_linkHover_${color + isPlayer}`;
                const flag = `ikaeasy_flag_${color}_animated${isPlayer}`;

                $city.find('.hover').addClass(hoverLink + `${colorKind === 'player' ? ' userMarked' : ''}`);
                $city.find('.link_img').addClass(link);
                $city.find('.link_img .flag').addClass(flag);
            }
        };

        const list = Marker.name;
        const name = colorKind === 'ally' ? 'ownerAllyTag' : 'ownerName';

        for(let i = 0; i < GamePlay.TOWN_SPOTS; i++) {
            let $city = $(`#cityLocation${i}`);
            if (!$city.hasClass('treaty')) {
                let info = this.island.cities[i];

                if ((!info[name]) || (info.type !== 'city')) {
                    continue;
                }

                info[name] = info[name].trim();
                _.each(list, (v, color) => {
                    _.each(v, (a) => {
                        if (a.endsWith('_ally')) {
                            a = a.replace('_ally', '');
                        }
                        if (a.trim() === info[name]) {
                            setClasses($city, color, colorKind);
                        }
                    });
                });

                if (removedMarker) {
                    removeMarker(colorKind);
                }
            }
        }
    }

    sendWorld(force = false) {
        if (this._worldUpdating) {
            return;
        }

        let cities = this.island.cities;

        if ((!getItem(`island_v2_${this.islandId}`)) || (force)) {
            let score = {};
            let activeCities = 0;
            this._worldUpdating = true;

            this.getUsersInfo((users) => {
                _.each(cities, function(city) {
                    if (city.type === "city") {
                        activeCities++;
                        score[city.ownerId] = users[city.ownerId]['s'];
                    }
                }.bind(this));

                let result = _.clone(this.island);
                result['server'] = getServerDomain();
                result['world'] = getServerWorld().substring(1);
                result['score'] = score;

                IkalogsRu.sendWorld(result);
                setItem(`island_v2_${this.islandId}`, 'true', 86400);

                let islandsData = getItem('islands_data') || {};
                islandsData[this.islandId] = {
                    count: activeCities
                };

                setItem('islands_data', islandsData);

                this._worldUpdating = false;
            });
        }
    }

    showIslandInfo(updateLevel = true) {
        if (!this._worldUpdating) {
            this.getUsersInfo();
        }

        let cities = this.island.cities;
        let now = Math.floor(_.now() / 1000);
        let users = getItem('users') || {};
        let active_cities= 0;

        if ($('.ikaeasy_score').length === 0) {
            _.each(cities, (city, index) => {
                if (city.type === "city") {
                    active_cities++;

                    if (this.options.get('island_details', true)) {
                        let $city = $(`#cityLocation${index}`);
                        let $cityTitle = $(`#js_cityLocation${index}TitleText`);

                        let ally = (city.ownerAllyTag) ? ` [${city.ownerAllyTag}]` : '';
                        let cityNewTitle = [`<span class="ikaeasy_city_title">${$cityTitle.html()}${ally}</span>`, ' <span class="ikaeasy_score"></span>'];

                        if ((this.options.get('island_ap', true)) && (city.level)) {
                            let ap = Math.floor(city.level / 4 + 3) - (($city.hasClass("own")) ? 0 : 2);
                            cityNewTitle.push(`<span class="ikaeasy_BD">${ ap }</span>`);
                        }

                        $cityTitle.html(cityNewTitle.join(''));
                        if ((updateLevel) && (city.level)) {
                            $city.append(`<div class="ikaeasy_levelcity">${city.level}</div>`);
                        }

                        this.recalcWidth(index);
                    }

                    if (this.options.get('island_ships_owner', true)) {
                        let $ships = $(`#js_cityLocation${index}Ships.fleetAction`);

                        if (($ships.length) && (!$ships.find('.ikaeasy_island_cityInfo_ships_name').length)) {
                            let ships_username = _.last($ships.attr('title').split(' '));
                            $ships.append(`<div class="ikaeasy_island_cityInfo_ships_name">${ships_username}</div>`);
                        }
                    }
                }
            });
        }

        this.getUsersInfo((users) => {
            _.each(cities, (city, index) => {
                if (this.options.get('island_details', true)) {
                    if (city.type === 'city') {
                        let $score = $(`#js_cityLocation${index}TitleText span.ikaeasy_score`);
                        $score.html(`#${numberToBeauty(users[city.ownerId].s)}`);
                        this.recalcWidth(index);
                    }
                }
            });

            let islands_data = getItem('islands_data') || {};
            if ((islands_data[this.islandId]) && (islands_data[this.islandId].count !== active_cities)) {
                this.sendWorld(true);
            } else {
                this.sendWorld();
            }
        });
    }

    getUsersInfo(callback) {
        let users_req = {};
        let users = getItem('users') || {};
        let now = Math.floor(_.now() / 1000) - 3;
        let done = _.after(this.island.cities.length, () => {
            setItem('users', users, 86400);
            callback && callback(users);

            setTimeout(this.clearExpiredUsers.bind(this), 1000);
        });

        _.each(this.island.cities, (city) => {
            if (city.type === "city") {
                let ownerId = city.ownerId;

                if ((!users[ownerId]) || (!users[ownerId].s) || (users[ownerId].e < now)) {
                    if (!users_req[ownerId]) {
                        users_req[ownerId] = true;
                        $.get(`/index.php?view=cityDetails&destinationCityId=${city.id}&ajax=1`, (data) => {
                            data = JSON.parse(data)[1][1][1];

                            let score = $.trim(data.match(/id="js_selectedCityScore">([^<]+)</)[1].replace(/[\s]+/, ''));
                            let scoreInt = parseInt(score.replace(/[^\d]+/g, ''));
                            users[ownerId] = {
                                's' : scoreInt,
                                'e' : now + this._expireForUserScores(scoreInt),
                                'h' : _.trim(score)
                            };

                            done();
                        });

                        return;
                    }
                }
            }

            done();
        });
    }

    _expireForUserScores(score) {
        if (score < 10000) {
            return 300;
        }

        if (score < 100000) {
            return 600;
        }

        if (score < 1000000) {
            return 1800;
        }

        return 43200;
    }

    clearExpiredUsers(){
        let now = Math.floor(_.now() / 1000) - 3;
        let users = getItem('users') || {};
        _.each(users, (u, k) => {
             if (u.e < now) {
                 delete users[k];
             }
        });

        setItem('users', users, 86400);
    }

    recalcWidth(index) {
        let w = 9;
        let obj = $(`#js_cityLocation${index}TitleScroll`);
        $(obj).find('div').each(function() {
            w += parseInt($(this).width());
        });

        $(obj).css({width: w, left: 55 - w / 2});
    }

}


export default Island;
