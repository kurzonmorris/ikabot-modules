import Parent from './dummy.js';
import { getInt } from '../../utils.js';

class Page extends Parent {

    async init() {
        let $sidebar = $('#sidebar');
        let $li = $sidebar.find('.resources li');
        let res = $li.eq(0).attr("class");

        if ($sidebar.find(`.${res}`).length === 2) {
            $sidebar.find('h4:eq(0)').html(LANGUAGE.getLocalizedString('island_mine_next_level') + ':');
            let need = getInt($li.eq(0).text());
            let have = getInt($li.eq(1).text());

            const tpl = await this.render('island_mine', {
                res: res,
                have: have,
                need: need
            });

            $('#maxLevelNotReached').find('div:eq(0)').after(tpl);
        }
    }
}

export default Page;
