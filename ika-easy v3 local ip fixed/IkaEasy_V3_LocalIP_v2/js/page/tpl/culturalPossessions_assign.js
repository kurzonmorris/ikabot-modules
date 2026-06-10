import Parent from './dummy.js';

class Page extends Parent {

    init() {
        $('#moveCulturalGoods ul li input').each(function(index, item) {
            let $item = $(item);
            let id = $item.attr('id').match(/textfield_city_(\d+)/)[1];
            this._ieData.getCity(id).set('culturalGoods', parseInt($item.val()));
        });
    }

}

export default Page;
