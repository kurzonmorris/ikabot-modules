import Parent from './dummy.js';

class Page extends Parent {

    init() {
        $('#buildingGround #buildings li.building:not(.notResearched) .cannotbuild > a').each((k, el) => {
            let $a = $(el);
            let p = $a.parent().html().replace(/[()]+/g, '');
            $a.parent().addClass('ikaeasy-cannotbuild').html(p);
        });

        this.ikariamPremiumToggle([$('#buildingGround #buildings li.building:not(.notResearched) .cannotbuild > a')]);
    }

}

export default Page;
