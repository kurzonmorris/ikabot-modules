import Event from './event.js';
import Render from './templater.js';
import {draggable} from '../utils.js';

let activeWindow = null;

class Win extends Event {
    constructor(data, force = false) {
        super();

        if ((activeWindow) && (!force)) {
            activeWindow.remove();
        }

        $('.templateView .close').click();

        Render('helper-win', data).then((tpl) => {
            this.$el = $(tpl);

            $('#container').append(this.$el);

            this.$el.find('.ikaeasy-window-close').click(() => {
                this.remove();
            });

            draggable($('.ikaeasy-window-header', this.$el), this.$el);

            $(document).on('keydown.esc', (e) => {
                e.stopPropagation();
                if (e.keyCode === 27) {
                    this.remove();
                }
            });

            this.getContent().css('max-height', $('body').height() - 250);

            // Хак нужен чтобы окно сразу не закрывалось если был открыт templateView через ссылку в адресной строке браузера
            if (!force) {
                setTimeout(() => {
                    activeWindow = this;
                }, 500);
            }

            this.emit('ready');
            this.off('ready');
        });
    }

    setToCenter() {
        let left = $('body').width() / 2 - this.$el.width() / 2;
        this.$el.css('left', left);
    }

    getContent() {
        return this.$el.find('.ikaeasy-window-content');
    }

    remove() {
        $(document).on('.esc');
        this.$el.remove();

        activeWindow = null;
        this.emit('close');
    }

    static removeActiveWindow() {
        if (activeWindow) {
            activeWindow.remove();
        }
    }
}

export default Win;
