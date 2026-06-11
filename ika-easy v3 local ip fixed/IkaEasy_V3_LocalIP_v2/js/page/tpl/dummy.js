import Parent from '../common.js';
import Render from '../../helper/templater.js';
import { getItem, setItem, removeItem, execute_js } from '../../utils.js';

class Dummy extends Parent {
    constructor() {
        super();
        this.#init();
    }

    async #init() {
        await this.initBackspace();
        await this.init();

        let cb = getItem('open_building_callback');
        if (cb) {
            removeItem('open_building_callback');
            if (cb.building === name) {
                let time = 200 + _.random(50, 200);
                setTimeout(() => {
                    if (cb.callback.open === 'empire') {
                        if (navigator.page_bg.empireModule) {
                            navigator.page_bg.empireModule.toggle(true);

                            if (cb.callback.tab) {
                                navigator.page_bg.empireModule.changeTab(cb.callback.tab);
                            }
                        }
                    }
                }, time);
            }
        }
    }

    init() {

    }

    async updated() {
        await this.init();
    }

    premiumUpdated() {

    }

    refresh() {
        this._data = Front.data;
    }

    initBackspace() {
        let stopBack = false;
        $(document).on('keydown.backspace', (e) => {
            if ((e.keyCode === 8) && (!stopBack)) {
                $('#js_backlinkButton').click();
            }
        });

        $(document).on('focus.backspace', 'input, textarea', () => {
            stopBack = true;
        });

        $(document).on('blur.backspace', 'input, textarea', () => {
            stopBack = false;
        });
    }

    async render(tpl, data = {}) {
        return Render(tpl, data)
    }

    ikariamPremiumToggle(list) {
        if (!this.options.get('hide_premium', true)) {
            return;
        }

        let $premiumAdvisorSidebar = $('#premiumAdvisorSidebar');
        if ($premiumAdvisorSidebar.length) {
            let $ul = $premiumAdvisorSidebar.closest('ul');
            $premiumAdvisorSidebar.closest('.accordionItem').addClass('ikaeasy-premium-to-hide');

            if ($ul.children().length === 1) {
                $ul.addClass('ikaeasy-premium-to-hide');
            }
        }

        _.each(list, (it) => {
            $(it).addClass('ikaeasy-premium-to-hide');
        });

        let key = Front.tpl;
        let $el = $(`#${key}`).addClass('ikaeasy-premium-wrap');
        if ($el.data('premium-updated')) {
            return;
        }

        let expand = !!getItem(`toggle-premium-${key}`);
        $el.toggleClass('ikaeasy-hide-premium', !expand);
        $('#sidebar').addClass('ikaeasy-premium-wrap').toggleClass('ikaeasy-hide-premium', !expand);

        let $btn = $('<span class="ikaeasy-premium-btn"></span>');
        $('#js_mainBoxHeaderTitle', $el).append($btn);

        $btn.click(() => {
            expand = !expand;
            setItem(`toggle-premium-${key}`, expand);
            $el.toggleClass('ikaeasy-hide-premium', !expand);
            $('#sidebar').toggleClass('ikaeasy-hide-premium', !expand);

            execute_js('ikariam.templateView.mainbox.scrollbar.adjustSize();');
            setTimeout(function() {
                execute_js('ikariam.templateView.mainbox.scrollbar.adjustSize();');
            }, 200);
        });

        execute_js('ikariam.templateView.mainbox.scrollbar.adjustSize();');
        $el.data('premium-updated', true);
    }

    destroy() {

    }

    selfDestroy() {
        $(document).off('.backspace');
        this.destroy();
    }
}

export default Dummy;
