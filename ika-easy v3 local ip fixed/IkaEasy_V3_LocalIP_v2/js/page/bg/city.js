import Db from '../../helper/db.js';
import Parent from './dummy.js';
import { execute_js, setItem } from '../../utils.js';

class City extends Parent {
    init() {
        if ((!this.buildings) || (this.buildings.cityId !== this.getCityId())) {

        }

        this.premium();
        this.updateBuilds();
        this.watcher();
    }

    async premium() {
        return;
    }

    premiumUpdated() {
        this.premium();
    }

    getCityId() {
        return parseInt(this._data.city.id);
    }

    getBuildLevel(name) {
        if (!this._builds[name]) {
            return 0;
        }

        return this._builds[name][0].level || 0;
    }

    updateBuilds() {
        this._builds = this._city.buildings;

        if ((this._city.isOwn) && (this._builds['embassy'])) {
            let b = this._builds['embassy'][0];
            let emb = { title: b.name, pos_id: b.position, city_id: this.getCityId() };
            setItem('embassy', emb);
        }
    }

    async watcher() {
        this.__watcher_is_updating = true;
        this._fillWatcherMinus();

        $('#ikaeasy_builds').remove();

        if (!this.options.get('city_details', true)) {
            console.log('watcher return');
            return;
        }

        const db = Db.db();
        let $worldmap = $('#worldmap');
        let $parent = $('<div id="ikaeasy_builds"></div>');
        let location = $('#locations');
        $worldmap.append($parent);

        $parent.css({
            top: $(location).css('top'),
            left: $(location).css('left'),
            width: $(location).width(),
            height: $(location).height()
        });

        const buildKeys = Object.keys(this._builds);
        for(let i = 0; i < buildKeys.length; i++) {
            const name = buildKeys[i];
            const builds = this._builds[name];

            for(let j = 0; j < builds.length; j++) {
                const build = builds[j];

                if (typeof build.level === "undefined") {
                    return;
                }

                let b_coord = (build.completed) ? db.pos.constructionSite : db.pos[name];
                if (!b_coord) {
                    console.warn(`IKAEASY: Unknown building ${name}`);
                    continue;
                }
                let $position = $(`#position${build.position}`);

                // Создаем у каждого здания табличку для уровня, а так же блок с ресами
                const tpl = await this.render('city-buildingInfo', { build });
                let $block = $(tpl);
                $block.css({
                    left: parseInt($position.css('left')) + b_coord[0].x,
                    top: parseInt($position.css('top')) + b_coord[0].y
                });

                $parent.append($block);

                if (!this._city.isOwn) {
                    $(`#ikaeasy_watcher_${build.position}`).attr('class', 'ikaeasy_watcher build_gray');
                    $(`#ikaeasy_watcher_${build.position} .ikaeasy_watcher_buttons`).remove();
                }
            }
        }

        if (!this.options.get('city_building_tooltip')) {
            $parent.addClass('ikaeasy_watcher_no_tooltip');
        }

        this.__watcher_is_updating = false;
        this.updateWatcher();
    }

    updateWatcher() {
        if ((!this._city.isOwn) || (this.__watcher_is_updating)) {
            return;
        }

        if (!this.options.get('city_details', true)) {
            return;
        }

        this.__watcher_is_updating = true;

        this.updateBuilds();
        const db = Db.db();

        // Узнаем кол-во ресурсов в городе
        let sourceOnCity = this._city.resources;
        let production = this._city.production;

        // Проверяем, нет ли строящихся зданий?
        let bb_icon = ($('#locations .constructionSite').length > 0) ? 'build_blue' : 'build_green';

        _.each(this._builds, (builds, name) => {
            _.each(builds, async (build) => {
                if (typeof build.level === "undefined") {
                    return;
                }

                let $block = $(`#ikaeasy_watcher_${build.position}`);
                let b_source = db.source[name];
                let b_coord = db.pos[name];
                let b_lvl = parseInt(build.level);

                if ((!b_coord) || (!b_source)) {
                    console.warn(`IKAEASY: Unknown building ${name}`);
                    return;
                }

                if (build.completed) {
                    b_coord = db.pos.constructionSite;
                    b_lvl++;
                }

                const $tooltip = $('.ikaeasy_watcher_tooltip', $block);

                let sources_ok = true;
                let class_icon = 'build_red';

                if (b_source[b_lvl]) {
                    const resourcesTypesCount = Object.values(b_source[b_lvl]).length;
                    let enoughPercent = 0;
                    let tooltipData = [];
                    _.each(b_source[b_lvl], (v, k) => {
                        if (v === 0) {
                            return;
                        }

                        let cost = Math.floor(v * this.__watcher_minus[k]);
                        const currentRes = sourceOnCity[k];
                        let need = currentRes - cost;
                        const percentAdded= currentRes < cost ? currentRes / cost * 100 : 100;
                        enoughPercent += percentAdded / resourcesTypesCount;
                        tooltipData.push({
                            resource: k,
                            cost: cost,
                            need: need,
                            production: (need >= 0) ? 0 : production[k]
                        });

                        if (need < 0) {
                            sources_ok = false;
                        }
                    });

                    if (this.options.get('city_building_tooltip')) {
                        const tpl = await this.render('city-watcherTooltip', {
                            list: tooltipData,
                            ok: sources_ok,
                            build: build
                        });

                        let $line = $(tpl);
                        $tooltip.empty().append($line);
                    }

                    if (sources_ok) {
                        class_icon = (build.completed) ? 'build_gray' : bb_icon;
                    }

                    if (enoughPercent < 100 && enoughPercent > 0){
                        const circumference = 81.6814;
                        const progressStroke = circumference - enoughPercent / 100 * circumference;
                        $('.ikaeasy_watcher_circle-progress__circle', $block)
                          .attr('style',`stroke-dashoffset: ${progressStroke}`)
                          .addClass("ikaeasy_watcher_circle-progress-active");
                    }
                } else {
                    class_icon = 'build_gray';
                }

                if (build.completed) {
                    b_coord = db.pos.constructionSite;
                    let $position = $(`#position${build.position}`);
                    $block.css({
                        left: parseInt($position.css('left')) + b_coord[0].x,
                        top: parseInt($position.css('top')) + b_coord[0].y
                    });

                    class_icon += ' ikaeasy_watcher_construction';
                }
                // progress bar inspired by:
                // @link https://css-tricks.com/building-progress-ring-quickly/
                $(`#ikaeasy_watcher_${build.position}`).attr('class', `ikaeasy_watcher ${class_icon}`).show();
                $('.watche_down', $block).off('click').click(async (e) => {
                    if ($(e.currentTarget).css('cursor') === 'default') {
                        return;
                    }

                    const code = await this.render('js-confirmPopup', {
                        txtAreYouSure: LANGUAGE.getLocalizedString('city_confirm_downgrade'),
                        code: `ajaxHandlerCall('/index.php?action=CityScreen&function=demolishBuilding&actionRequest=${this._data.actionRequest}&currentCityId=${this.getCityId()}&cityId=${this.getCityId()}&position=${build.position}&level=${build.level}&backgroundView=city');`
                    });

                    execute_js(code);
                });

                $('.watche_up', $block).off('click').click((e) => {
                    if ($(e.currentTarget).css('cursor') === 'default') {
                        return;
                    }

                    this.upgradeBuilding(build.position, build.level);
                });

                $('.ikaeasy_watcher_title', $block).off('click').click((e) => {
                    this.openBuilding({
                        cityId: this.getCityId(),
                        building: build.building,
                        position: build.position
                    });
                });
            });
        });

        this.__watcher_is_updating = false;
    }

    _fillWatcherMinus() {
        this.__watcher_minus = this._city.getBuildingsCostDiscount();
    }
}

export default City;
