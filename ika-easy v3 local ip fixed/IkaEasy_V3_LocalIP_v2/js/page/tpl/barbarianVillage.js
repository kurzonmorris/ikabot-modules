import Parent from './dummy.js';

class Page extends Parent {

    async init() {
        let list = ['resource', 'tradegood1', 'tradegood2', 'tradegood3', 'tradegood4'];
        let sum = 0;
        _.each(list, (v) => {
            sum += this.getGoods(v);
        });

        let ships = Math.ceil(sum / 500);
        const tpl = await this.render('barbarianVillage', {sum: sum, ships: ships});
        $('.barbarianCityKingSpeech').html(tpl);
    }

    getGoods(id) {
        return parseInt($(`#js_islandBarbarianResource${id}`).html().replace(/[^0-9]+/g, ''));
    }

}

export default Page;
