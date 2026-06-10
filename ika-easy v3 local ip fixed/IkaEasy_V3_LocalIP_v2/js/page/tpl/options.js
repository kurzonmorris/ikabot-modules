import Parent from './dummy.js';
import { execute_js } from '../../utils.js';

class Page extends Parent {

    async init() {
        await this.addTab();
        this.$page = $('#tab_options');
    }

    async addTab() {
        let $tab = $('#js_tab_ikaeasy');
        if (!$tab.length) {
            const tpl = await this.render('options-tab');
            $tab = $(tpl);
            $tab.click(() => {
                this.selectTab();
            });
            $('#js_tab_options').after($tab);
        }
    }

    async selectTab() {
        $('#js_tab_ikaeasy').addClass('selected').siblings().removeClass('selected');

        const tpl = await this.render('options-page', { list: this.options.getList(), options: this.options });
        this.$page.html(tpl);

        this.$page.find('input[type="checkbox"]').change((e) => {
            let $input = $(e.currentTarget);
            let name = $input.attr('name');

            this.options.set(name, $input.prop('checked'));
        });

        execute_js('ikariam.templateView.mainbox.scrollbar.adjustSize();');

    }

}

export default Page;
