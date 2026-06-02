import Parent from '../../common.js';
import { execute_js } from '../../../utils.js';
import { CityType } from '../../../const.js';

class Dummy extends Parent {
    drawTimer;
    firstRender = true;
    drawUpdateFreq = 5000;

    constructor(parent, tpl) {
        super();

        this.tpl = `dummy/empire/tabs/${tpl}`;
        this.parent = parent;
        this.$parent = parent.$content;
        this.init();

        setTimeout(() => {
            this.draw();
            this.registerClickHandlers();
            this.afterFirstRender();
        }, 0);
    }

    init() {
    }

    updated() {
        this.init();
    }

    async draw() {
        return this.getRenderData(async (data, helpers) => {
            const tpl = await this.render(this.tpl, data, helpers);

            this.$el = $(tpl);
            this.parent.updateContent();

            this.afterRender();
            return true;
        });
    }

    startDrawTimer(){
        this.drawTimer = setInterval(()=>{
            this.draw();
        }, this.drawUpdateFreq);
    }

    afterFirstRender(){
        this.startDrawTimer();
    }

    stopDrawTimer(){
        clearInterval(this.drawTimer);
    }

    onClick(el, callback){
        return this.$parent.off("click", el).on('click', el, callback);
    }

    onHover(el, callback){
        return this.$parent.on('mouseover', el, callback);
    }

    registerClickHandlers(){
        if(!this.parent){
            console.error("[IkaEasy]: Cant register click events on empty element!");
            return;
        }
        this.onClick('td.empire_city span', (e) => {
            const $tr = $(e.currentTarget).closest('tr');
            if (!$tr.hasClass('current_city')) {
                this.changeCity($tr.data('id'));
            }
        });
        this.onClick('td.empire_transport [data-js]', (e) => {
            const $tr = $(e.currentTarget).closest('tr');
            if (!$tr.hasClass('current_city')) {
                this.parent.close();

                const js = $(e.currentTarget).data('js');
                execute_js(js);
            }
        });

        this.onRegisterClickHandlers(this.$parent);
    }

    onRegisterClickHandlers($el){
        // used in child
    }

    _prepareCities() {
        const cities = [];
        _.each(this._data.cities, (city, key) => {
            if ((key.indexOf('city_') === 0) && (city.relationship === CityType.OWN)) {
                city.id = parseInt(city.id);
                cities.push(city);
            }
        });

        this._cities = Object.freeze(cities);
    }

    async getRenderData(callback) {
        await callback({});
    }

    afterRender() {
        // child use
    }

    afterFirstRender(){
        // child use
    }

    refresh() {
        super.refresh();
    }

    destroy() {//shouldnt be overriden - use onDestroy() instead
        this.stopDrawTimer();
        this.onDestroy();
    }
    onDestroy(){
    }
}

export default Dummy;
