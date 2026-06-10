import Win from '../../helper/win.js';
import Parent from './dummy.js';
import { addToLeftMenu } from '../../utils.js';

class Module extends Parent {
    init() {
        if (!$('.ikaeasy-empire').length) {
            this.addLeftMenu();
        }
    }

    updated() {
        if (this.activeModule) {
            this.activeModule.refresh();
            this.activeModule.updated();
        }
    }

    createWindow() {
        this._window = new Win({
            title: LANGUAGE.getLocalizedString('dummy_empire').replace('<br/>', ' '),
            width: '90%'
        });

        this._window.on('close', () => {
            this._window = null;
            this.activeModule && this.activeModule.destroy();
            this.activeModule = null;
        });

        this._window.on('ready', async () => {
            this._window.getContent().addClass('ikaeasy-empire-wrapper');

            const tpl = await this.render('dummy/empire/window');
            const $el = $(tpl);
            this._window.getContent().empty().append($el);
            this.$content = this._window.getContent().find('#tabReport');

            $el.find('.tab').click((e) => {
                this.changeTab($(e.currentTarget).data('tab'));
            });

            this.changeTab('resources');
        });
    }

    close() {
        if (!this._window) {
            return;
        }

        this._window.remove();
    }

    updateContent() {
        if (this.activeModule) {
            this.$content.empty().append(this.activeModule.$el);
        }
    }

    async changeTab(tab) {
        if (!this._window) {
            return;
        }

        let $tab = this._window.getContent().find(`.tabmenu .tab[data-tab="${tab}"]`);
        if ($tab.hasClass('selected')) {
            return;
        }

        $tab.addClass('selected').siblings().removeClass('selected');
        this.activeModule && this.activeModule.destroy();

        try {
            let {default: module} = await import(`../../../js/page/modules/empire/${tab}.js`);
            this.activeModule = new module(this);
        } catch (error) {
            console.log(error);
        }
    }

    async addLeftMenu() {
        const el = await addToLeftMenu('image_empire', LANGUAGE.getLocalizedString('dummy_empire'), false, this.options.get('quick_menu'));
        const $li = el;
        $li.addClass('ikaeasy-empire');
        $li.click(() => {
            this.toggle();
        });
    }

    toggle(b) {
        if (typeof b !== 'boolean') {
            b = !this._window;
        }

        if (b) {
            if (!this._window) {
                this.createWindow();
            }
        } else {
            this._window && this._window.remove();
        }
    }


    destroy() {
        this.activeModule && this.activeModule.destroy();
    }
}

export default Module;
