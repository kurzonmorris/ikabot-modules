import Parent from './dummy.js';
import Tooltip from '../../../helper/tooltip.js';
import Storage from '../../../helper/storage.js';

class Module extends Parent {
    $loader;
    loaderEl = '#empire_sync';
    constructor(parent) {
        super(parent, 'resources.ejs')
    }

    init() {
    }

    async getRenderData(callback) {
        const data = {
            cities: this._cities,
            selectedCityId: this._data.cities.selectedCityId,
            manager: Front.ikaeasyData
        };

        await callback(data);
    }

    afterRender() {
        this.getLoader();
        this.syncAll();
    }

    getLoader(){
        this.$loader = $(this.loaderEl);
    }

    startLoader(){
        if (this.$loader.hasClass('rotate')) {
            return;
        }

        this.$loader.addClass('rotate');
    }

    async syncAll(force = false) {
        const lastUpdateStorage = await Storage.get("empire");
        const nowTime = _.now();
        if(lastUpdateStorage && !force){
            const lastUpdate = lastUpdateStorage.update_time;
            const oneMinute = 1 * 60000;
            const tenMinutes = oneMinute * 10;
            if(this.firstRender){
                this.firstRender = false;
                if(lastUpdate + oneMinute > nowTime){ return; }
            }else{
                if(lastUpdate + tenMinutes > nowTime){ return;}
            }
        }

        this.stopDrawTimer();
        this.startLoader();//loader will stop after re-render
        await Storage.set("empire", {
            "update_time": nowTime
        });

        await Front.ikaeasyData.ajaxUpdateAllCities();
        await Front.ikaeasyData.ajaxUpdatePalace();
        this.draw();
        this.startDrawTimer();
    }

    onRegisterClickHandlers($el){
        this.onClick(this.loaderEl, (e) => {
            e.preventDefault();
            this.startLoader();
            this.syncAll(true);
        });

        this.onHover('tbody td.empire-resource', async (e) => {
            let $td = $(e.currentTarget);
            if (!$td.data('resource')) {
                return;
            }

            let data = {
                resource : $td.data('resource'),
                amount   : $td.data('amount'),
                safe     : $td.data('safe'),
                capacity : $td.data('capacity'),
                percent : $td.data('percent'),
                production: $td.data('production'),
                hasProduction: $td.data('has-production')
            };

            const tpl = await this.render('dummy/empire/tooltip/resource', data);
            Tooltip.show(e, $td, $(tpl));
        });

        this.onHover('tbody td.empire-research', async (e) => {
            let $td = $(e.currentTarget);
            let cityId = $td.parent().data('id');
            let city = Front.ikaeasyData.getCity(cityId);

            const tpl = await this.render('dummy/empire/tooltip/research', { city: city });
            Tooltip.show(e, $td, $(tpl));
        });

        this.onHover('tbody td.empire-happiness', async (e) => {
            let $td = $(e.currentTarget);
            let cityId = $td.parent().data('id');
            let city = Front.ikaeasyData.getCity(cityId);
            let popData = city.getPopulationData();

            if (popData) {
                const tpl = await this.render('dummy/empire/tooltip/happiness', { popData: popData });
                Tooltip.show(e, $td, $(tpl));
            }
        });

        this.onHover('tbody td.empire-corruption', async (e) => {
            let $td = $(e.currentTarget);
            const tpl = await this.render('dummy/empire/tooltip/corruption', { });
            Tooltip.show(e, $td, $(tpl));
        });
    }

    onDestroy() {
        Tooltip.hide();
    }
}

export default Module;
