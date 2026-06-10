import { VERSION } from './const.js';
import Event from './helper/event.js';
import IkalogsRu from './helper/ikalogsRu.js';
import Whatnews from './helper/whatnews.js';
import Win from './helper/win.js';
import Manager from './data/Manager.js';
import { Resources, TradeGoodOrdinals } from './const.js';
import { execute_js } from './utils.js';
import sandbox from './sandbox.js';

class Navigation extends Event {
    constructor() {
        super();

        this.bg = null;
        this.tpl = null;
        this.data = null;
        this.ikaeasyData = null;
        this.app_version = null;

        window.addEventListener('message', (event) => {
            if (event.source !== window) {
                return;
            }

            if ((event.data.type) && ((event.data.type === 'FROM_IKAEASY_V3'))) {
                if (event.data.cmd === 'update') {
                    let tpl = this.tpl;
                    let bg = this.bg;

                    this.bg = event.data.bg;
                    this.tpl = event.data.tpl;
                    this.data = event.data.data || this.data;
                    this.viewData = event.data.viewData || null;

                    if (!this.ikaeasyData) {
                        this.ikaeasyData = new Manager(() => {
                            this._update(bg, tpl);
                        });

                        chrome.runtime.sendMessage({ cmd: 'get-version' }, (version) => {
                            this.app_version = version;
                            Whatnews.init(this.app_version);
                        });
                    } else {
                        this._update(bg, tpl);
                    }
                } else if (event.data.cmd === 'form') {
                    this.emit('form', [event.data.form]);
                }else if(event.data.cmd === 'ajax'){
                    this.emit('ajax', [event.data.request]);
                }
            }
        }, false);
        this.httpListenerHandler();

        chrome.runtime.sendMessage({ cmd: 'get-version' }, (version) => {
            this.app_version = version;
        });

        $('head').append(`<link href="${chrome.runtime.getURL('css/ikaeasy.css')}?${VERSION}"  rel="stylesheet" type="text/css" />`);
        $('head').append(`<script type="text/javascript" src="${chrome.runtime.getURL('inner/ikaeasy.js')}"></script>`);

        IkalogsRu.getMines().then(data => {
            window.islandsInfo = data;

            // этот код использовался для первичной отрисовки
            // $('#map1 .islandTile').toArray().forEach(island => {
            //     const $island = $(island);

            //     const [name] = $island.attr('title').split(' ');

            //     const [wood, mine, wonder] = data[name] || ['-', '-', '-'];

            //     const $mine = $island.find('.tradegood');
            //     const $wonder = $island.find('.wonder');

            //     $mine
            //         .append(`<span class="ikaeasy-resource ikaeasy-l--20">${mine}</span>`);

            //     $wonder
            //         .append(`<span class="ikaeasy-resource ikaeasy-t-20 ikaeasy-l--20">${wonder}</span>`);

            //     $island
            //         .append(`<div class="ikaeasy-resource ikaeasy-b-70 ikaeasy-l-55"><div class="ikaeasy-resource-icon ikaeasy-w-24 ikaeasy-h-20" style="background-image: url(skin/resources/icon_wood.png);"></div><span>${wood}</span></div>`)
            // });
        });
    }

    async _update(bg, tpl) {
        console.info(`Background: ${this.bg} / ${!!this.page_bg_initialized}, template: ${this.tpl}`);

        let next = async () => {
            if ((tpl !== this.tpl) || (!this.page_tpl)) {
                if (this.tpl) {
                    Win.removeActiveWindow();
                }

                if (this.page_tpl) {
                    this.page_tpl.selfDestroy();
                }

                this.page_tpl = await this.initPage(this.tpl, 'tpl');
            } else if (this.page_tpl) {
                this.page_tpl.refresh();
                this.page_tpl.updated();
            }

            sandbox.send('set_params', {Front: {data: this.data, ikaeasyDataJson: this.ikaeasyData.getJson()}});
        }

        if ((bg !== this.bg) || (!this.page_bg_initialized)) {
            if (this.page_bg) {
                this.page_bg.selfDestroy();
            }

            this.page_bg_initialized = true;
            this.page_bg = await this.initPage(this.bg, 'bg');
            next();
        } else if (this.page_bg) {
            this.page_bg.refresh();
            this.page_bg.updated();
            next();
        }
    }

    async initPage(name, path) {
        if (typeof name !== 'string') {
            return;
        }

        try {
            let {default: page} = await import(`../js/page/${path}/${name}.js`);

            if (!page) {
                return null;
            }

            return new page(this, name);

        } catch (error) {
            console.log(error);
            return null;
        }
    }

    premiumUpdated() {
        this.page_bg && this.page_bg.premiumUpdated();
        this.page_tpl && this.page_tpl.premiumUpdated();
    }

    httpListenerHandler(){
        this.on('ajax', (request)=>{
            if(!request.data || request.data === ''){ return; }
            const data = typeof request.data === 'string' ? JSON.parse(request.data) : request.data;
            this._parseObject(data);
        });
    }

    _parseObject = (arr) => {
        arr.forEach((data) => {
            if ((Array.isArray(data)) && (data.length === 2)) {
                let closure = this[`handle_${data[0]}`];
                if (typeof closure === 'function') {
                    closure(data[1]);
                }
            }
        });
    }

    handle_changeView = async (dataArr) => {
        if(!(Array.isArray(dataArr) || dataArr.length > 1)
            && !(dataArr[0] && typeof dataArr[0] === 'string')
            ){
                return;
            }

        const fileName = dataArr[0];
        const rawHtml = dataArr[1];

        try {
            let {default: controller} = await import(`../js/page/tpl/controllers/${fileName}.controller.js`);

            if (!controller) {
                return null;
            }

            controller.ajaxHTML = rawHtml;
            controller.init();
            return true;
        } catch (error) {
            console.error(error);
            return null;
        }
    }


    handle_updateGlobalData = (data) => {
        if (data.actionRequest) {
            this.data.actionRequest = data.actionRequest;
            execute_js(`ikariam.model.actionRequest='${data.actionRequest}';`);
        }

        let bgData = data.backgroundData || {};
        let headData = data.headerData || {};
        let cityId = parseInt(bgData.id);
        let city = this.ikaeasyData.getCity(cityId);

        let list = ['isCapital', 'islandId', 'name', 'ownerId', 'ownerName', 'maxActionPoints'];

        list.forEach((v) => {
            if (typeof bgData[v] !== 'undefined') {
                city._data[v] = bgData[v];
            } else if (typeof headData[v] !== 'undefined') {
                city._data[v] = headData[v];
            }
        });

        if (city._data.ownerId) {
            city._data.ownerId = parseInt(city._data.ownerId);
        }

        if (bgData.position) {
            city._data.buildings = city._getBuilds(bgData.position);
        }

        if (headData.maxResources) {
            city._data.maxResources[Resources.WOOD]   = headData.maxResources.resource;
            city._data.maxResources[Resources.WINE]   = headData.maxResources[TradeGoodOrdinals.WINE];
            city._data.maxResources[Resources.MARBLE] = headData.maxResources[TradeGoodOrdinals.MARBLE];
            city._data.maxResources[Resources.SULFUR] = headData.maxResources[TradeGoodOrdinals.SULFUR];
            city._data.maxResources[Resources.GLASS]  = headData.maxResources[TradeGoodOrdinals.GLASS];
        }

        if (headData.currentResources) {
            city._data.resources[Resources.WOOD]       = headData.currentResources.resource;
            city._data.resources[Resources.WINE]       = headData.currentResources[TradeGoodOrdinals.WINE];
            city._data.resources[Resources.MARBLE]     = headData.currentResources[TradeGoodOrdinals.MARBLE];
            city._data.resources[Resources.SULFUR]     = headData.currentResources[TradeGoodOrdinals.SULFUR];
            city._data.resources[Resources.GLASS]      = headData.currentResources[TradeGoodOrdinals.GLASS];
            city._data.resources[Resources.CITIZENS]   = headData.currentResources[Resources.CITIZENS];
            city._data.resources[Resources.POPULATION] = headData.currentResources[Resources.POPULATION];
        }

        if (headData.producedTradegood) {
            let islandResource = _.findKey(TradeGoodOrdinals, (v) => { return v === parseInt(headData.producedTradegood); });

            city._data.production = {}; // Это мы зачищаем, потому что возможен перенос города с одного острова на другой
            city._data.production[Resources.WOOD] = Math.floor(headData.resourceProduction * 3600);
            city._data.production[Resources[islandResource]] = Math.floor(headData.tradegoodProduction * 3600);
            city._data.production[Resources.WINE_SPENDING] = headData.wineSpendings;

            city._data.productionEQ = {};
            city._data.productionEQ[Resources.WOOD] = headData.resourceProduction;
            city._data.productionEQ[Resources[islandResource]] = headData.tradegoodProduction;

            city._updateWineSpending(headData.wineSpendings);
        }

        city._data.updatingTime = _.now();
        city.save();
    }
}

window.Front = new Navigation();
