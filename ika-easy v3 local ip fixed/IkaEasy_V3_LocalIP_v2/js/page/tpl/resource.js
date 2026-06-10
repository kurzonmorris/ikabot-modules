import Parent from './dummy.js';
import { getInt } from '../../utils.js';

class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle([$('#resource .premiumOfferBox')]);

        let $li = $('.resUpgrade .resources li');
        let res = $li.eq(0).attr("class");

        if ($(`.resUpgrade .${res}`).length === 2) {
            $('.resUpgrade h4:eq(0)').html(LANGUAGE.getLocalizedString('island_mine_next_level') + ':');
            let need = getInt($li.eq(0).text());
            let have = getInt($li.eq(1).text());

            const tpl = await this.render('island_mine', { res: res, have: have, need: need });
            $('div.resUpgrade ul.resources:last').after(tpl);
        }
    }
}

export default Page;
