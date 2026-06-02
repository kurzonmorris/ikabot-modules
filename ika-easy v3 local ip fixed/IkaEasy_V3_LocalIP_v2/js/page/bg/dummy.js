import Support from '../../helper/support.js';
import Parent from '../common.js';
import { Resources, CityType } from '../../const.js';
import {
    getItem,
    setItem,
    sendNotification,
    transformHours,
    formatNumber,
    getServerId,
    getServerWorld,
    draggable,
    execute_js,
    createDynamicWin,
    addToLeftMenu
} from '../../utils.js';

const MAP_URL = 'https://ikalogs.ru/tools/map/';
class Dummy extends Parent {
    constructor() {
        super();

        this.__inited = false;
        this.#init();
    }

    async #init() {
        if (!this.__inited) {
            this.init();
            await this.support();
            await this.openMap();
            this.nextCity();
            await this.getProduction();
            this.goldConsumption();
            await this.addOtherButtons();
            await this.transporter();
            this.smallTweaks();
            this.notes();
            this.initCityHotkeys();
            this.checkAdvisor();
            this.quickMenu();

            if (this.options.get('empire', true)) {
                this.empireModule = this.createModule('empire');
            }

            if (getItem('open_building')) {
                this.openBuilding();
            }

            this.__inited = true;
        }
    }

    init() {

    }

    _updateData() {
        super._updateData();
        this._city.update();
    }

    /**
     * Дабы из перезаписываемой функции не дергать родительскую функцию
     */
    refresh() {
        if (!this.__inited) {
            setTimeout(() => {
                this.refresh();
            }, 30);
            return;
        }

        super.refresh();

        setTimeout(() => {
            this.goldConsumption();
        }, 100);

        this.transporter();
        this.getProduction();
        this.addOtherButtons();
        this.checkAdvisor();

        if (getItem('open_building')) {
            this.openBuilding();
        }
    }

    updated() {
        this.init();
    }

    premiumUpdated() {

    }

    checkAdvisor() {
        if (!this.options.get('notification_advisor', false)) {
            return;
        }

        let advisor = {};
        _.each(Front.data.advisorData, (v, k) => {
            if (v.cssclass) {
                advisor[k] = (v.cssclass.indexOf('active') > -1);
            }
        });

        let current_advisors = getItem('advisor_status') || {};
        _.each(advisor, (v, k) => {
            if ((v) && (!current_advisors[k])) {
                let params = {
                    action: 'advisor',
                    advisor: k,
                    random: _.now()
                };

                sendNotification('notif.advisor_' + k, 'notif.advisor_updated', params);
            }
        });

        setItem('advisor_status', advisor);
    }

    nextCity() {
        if ($('#ikaeasy_nextCity').length) {
            return;
        }

        if (this._cities.length < 2) {
            return;
        }

        let $nextCity = $('<li class="ikaeasy_nextCity" id="ikaeasy_nextCity"></li>');
        $('#cityResources .resources').prepend($nextCity);

        $nextCity.click(() => {
            let currentId = this._data.cities.selectedCityId;
            let current = _.findIndex(this._cities, {id: currentId});
            let nextCity = this._cities[current + 1] || this._cities[0];

            this.changeCity(nextCity.id);
        });
    }

    async getProduction() {
        if (!this.options.get('dummy_resource_prod', true)) {
            return;
        }

        $('.ikaeasy_delete_me').remove();

        const resCol = [Resources.WOOD, Resources.WINE, Resources.MARBLE, Resources.GLASS, Resources.SULFUR];

        for (let i = 0; i < resCol.length; i++) {
            const resource = resCol[i];

            let cnt = this._city.production[resource] || 0;
            let wineLeftTime = 0;

            if (resource === Resources.WINE) {
                cnt -= this._city.production[Resources.WINE_SPENDING];

                if (cnt < 0) {
                    wineLeftTime = Math.abs(this._city.resources[Resources.WINE] / cnt);
                    wineLeftTime = transformHours(wineLeftTime);

                    const tpl = await this.render('dummy-wineSpending', {wineLeftTime: wineLeftTime, cnt: cnt});
                    $(`#js_GlobalMenu_wine_tooltip table tbody tr:eq(1)`).after(tpl);
                }
            }

            if ((typeof this._city.production[resource] !== 'undefined') || (resource === Resources.WINE)) {
                let cntFormatted = formatNumber(cnt);
                const tpl = await this.render('dummy-resourceProd', {
                    cnt: cnt,
                    cntFormatted: cntFormatted,
                    resource: resource,
                    wineLeftTime: wineLeftTime
                });

                $(`#resources_${resource}`).append(tpl);
            }
        }
    }

    goldConsumption() {
        if (($('#IkaEasy_Gold_per_hour').length) || (!(this.options.get('dummy_resource_prod', true)))) {
            return;
        }

        const $goldTd = $('#js_GlobalMenu_gold_Calculation');
        let gold = 0;
        if ($goldTd.length) {
            gold = parseInt($goldTd.text().trim().replace(/\D/g, ''));
            console.log('gold', gold)

        } else {
            gold = parseInt(this._data.gold.income + this._data.gold.scientistsUpkeep + this._data.gold.upkeep + this._data.gold.badTaxAccountant);
        }

        $("#js_GlobalMenu_gold").append(`<span id="IkaEasy_Gold_per_hour" class="ikaeasy_delete_me ikaeasy_${(gold >= 0) ? 'green' : 'red'}">${formatNumber(gold)}</span>`);
    }

    async addOtherButtons() {
        let emb = getItem('embassy');
        if (emb) {
            // Кнопка на открытие посольства
            let $embassy = $('#leftMenu .image_embassy').closest('li');
            if (!$embassy.length) {
                $embassy = await addToLeftMenu('image_embassy', emb.title, false, this.options.get('quick_menu'));

                $embassy.click(() => {
                    this.openBuilding({
                        building: 'embassy',
                        cityId: emb.city_id,
                        position: emb.pos_id
                    });
                });
            }
        }
    }

    async support() {
        let $li = await addToLeftMenu('image_support', LANGUAGE.getLocalizedString('support_text'), true, this.options.get('quick_menu'));
        $li.click(() => {
            Support.show();
        });
    }

    async openMap() {
        let usernick = $('#GF_toolbar .avatarName a.noViewParameters').attr('title');
        let $li = await addToLeftMenu('image_map', LANGUAGE.getLocalizedString('open_map_text'), true, this.options.get('quick_menu'));
        $li.click(() => {
            window.open(`${MAP_URL}?server=${getServerId()}&world=${getServerWorld().substring(1)}&search=city&nick=${encodeURIComponent(usernick)}`, '_blank');
        });
    }

    async transporter() {
        if (!this.options.get('dummy_transporter', true)) {
            return;
        }

        if ($('#ikaeasy_transporter').length) {
            $(`#ikaeasy_transporter .current_city`).removeClass('current_city');
            $(`#ikaeasy_transporter [data-id="${this._data.cities.selectedCityId}"]`).addClass('current_city');
            return;
        }

        if (this._cities.length < 2) {
            return;
        }

        let $window = $('<div class="ikaeasy_dynamic"></div>');
        $('body').append($window);

        let pos = getItem('transporter_position');
        if (pos) {
            pos.top = Math.max(20, pos.top);
            pos.left = Math.max(20, pos.left);

            $window.css(pos);
        }

        let $cities = $('<div class="ikaeasy_transporter" id="ikaeasy_transporter"></div>');
        let $notMyCities = null;

        const cityKeys = Object.keys(this._cities);
        for (let i = 0; i < cityKeys.length; i++) {
            const key = cityKeys[i];
            const city = this._cities[key];

            let isCurrent = (city.id === this._data.cities.selectedCityId);

            const tpl = await this.render('dummy-transporterCity', { city: city, current: isCurrent });
            let $city = $(tpl);

            $city.on('click', '.empire_city>span', () => {
                if (!$city.hasClass('current_city')) {
                    this.changeCity(city.id);
                }
            });

            $city.on('click', '[data-js]', (e) => {
                if (!$city.hasClass('current_city')) {
                    let js = $(e.currentTarget).data('js');
                    execute_js(js);
                }
            });

            if (city.relationship !== CityType.OWN) {
                if (!$notMyCities) {
                    $notMyCities = $('<div id="ikaeasy_not_mycities"></div>');
                }

                $notMyCities.append($city);
            } else {
                $cities.append($city);
            }
        }

        if ($notMyCities) {
            $cities.append('<div class="box_border" id="ikaeasy_box_border"></div>');
            $cities.append($notMyCities);

            $('#ikaeasy_box_border', $cities).click(function() {
                $notMyCities.slideToggle('fast', function() {
                    setItem('transporter_is_show_not_my', $notMyCities.is(':visible'));
                });
            });

            if (getItem('transporter_is_show_not_my')) {
                setTimeout(() => {
                    $notMyCities.show();
                }, 50);
            }
        }

        let $dynamicWin = await createDynamicWin(LANGUAGE.getLocalizedString('dummy_transporter'));
        $window.append($dynamicWin);

        function toggleWindow(b) {
            if (typeof b !== 'boolean') {
                b = !getItem('transporter_is_show');
            }

            if (b) {
                $window.show();
                $dynamicWin.find('.dynamic').append($cities);
                $li.removeClass('ikaeasy-transporter-left');
            } else {
                $window.hide();
                $li.addClass('ikaeasy-transporter-left').append($cities);
            }

            setItem('transporter_is_show', b);
        }


        let $li = await addToLeftMenu('image_transporter', LANGUAGE.getLocalizedString('dummy_transporter'), false, this.options.get('quick_menu'));
        $li.click((e) => {
            let $el = $(e.target);
            if (($el.hasClass('ikaeasy_transporter')) || ($el.closest('.ikaeasy_transporter').length)) {
                return;
            }

            toggleWindow();
        });

        $('.indicator', $window).click(function(e) {
            e.preventDefault();
            toggleWindow(false);
        }.bind(this));

        toggleWindow(getItem('transporter_is_show'));

        draggable($('.dynamic_title', $window), $window, () => {
            setItem('transporter_position', $window.offset());
        });
    }

    addLinkToIslandFeature() {
        let $resourceType = $('ul.resources li div p:first-child');
        let islandId = this._data.island.islandId;

        // Добавляем ссылку для дерева
        $('#resources_wood').css('cursor', 'pointer').attr('onClick', `ajaxHandlerCall('?view=resource&type=resource&islandId=${islandId}'); return false;`);

        // Добавляем ссылку для Драгоценного ресурса
        $resourceType.not(".invisible").eq(1).parent().parent().css('cursor', 'pointer').attr('onClick', `ajaxHandlerCall('?view=tradegood&islandId=${islandId}'); return false;`);
    }

    smallTweaks() {
        if (this.options.get('auto_accept_daily_bonus', true)) {
            if ($("#dailybonus").length) {
                execute_js('ajaxHandlerCallFromForm($("#dailybonus")[0]); $("body").trigger("click.dropDown"); ikariam.getMultiPopupController().closePopup();');
            }
        }

        if (this.options.get('hide_ads', true)) {
            $('body').addClass('ikaeasy-hide-ads');
        }

        if (this.options.get('hide_friends_bar', false)) {
            $('body').addClass('ikaeasy-hide-friends');
        }

        if (this.options.get('hide_happy_hour', false)) {
            $('#btnIngameCountdown1.happyHour').parent().hide();
        }
    }

    notes() {
        if ((this.options.get('notes')) && (!$('#GF_toolbar li.notes').data('ikaeasy'))) {
            this.createModule('notes');
        }
    }

    initCityHotkeys() {
        if (!this.options.get('city_hotkeys', true)) {
            return;
        }

        const hotkeys = [49, 50, 51, 52, 53, 54, 55, 56, 57, 48, 189, 187];
        let stopShortcut = false;
        $(document).on('keydown.hotkeys', (e) => {
            let index = hotkeys.indexOf(e.keyCode);
            if ((!stopShortcut) && (index > -1)) {
                if (e.shiftKey) {
                    if (['+', '_'].indexOf(e.key) > -1) {
                        // get current index
                        const currentIndex = this._cities.findIndex(city => city.id === this._data.cities.selectedCityId);
                        let nextIndex = -1;

                        if (e.key === '+') {
                            // next city
                            nextIndex = currentIndex + 1;
                            if (nextIndex >= this._cities.length) {
                                nextIndex = 0;
                            }
                        } else {
                            // prev city
                            nextIndex = currentIndex - 1;
                            if (nextIndex < 0) {
                                nextIndex = this._cities.length - 1;
                            }
                        }

                        if (nextIndex > -1) {
                            const city = this._cities[nextIndex];
                            if (city) {
                                this.changeCity(city.id);
                            }
                        }
                    }
                } else {
                    if (typeof this._cities[index] !== 'undefined') {
                        let city = this._cities[index];
                        if (city.id === this._data.cities.selectedCityId) {
                            return;
                        }

                        this.changeCity(city.id);
                    }
                }
            }
        });

        $(document).on('focus.hotkeys', 'input, textarea', () => {
            stopShortcut = true;
        });

        $(document).on('blur.hotkeys', 'input, textarea', () => {
            stopShortcut = false;
        });
    }

    quickMenu() {
        if (this.options.get('quick_menu')) {
            $('li.expandable').addClass('expandable-quick').hover(
              function() {
                  $(this).addClass('hover');
              }, function() {
                  $(this).removeClass('hover');
              }
            );
        }
    }

    destroy() {
        super.destroy();
    }

    selfDestroy() {
        this.destroy();
    }
}

export default Dummy;
