import Parent from './dummy.js';
import { getItem, removeItem } from '../../utils.js';

class Page extends Parent {

    init() {
        $(document).off('.backspace');

        let $js_msgTextConfirm = $('#js_msgTextConfirm');
        $js_msgTextConfirm.attr('placeholder', LANGUAGE.getLocalizedString('dummy_message_placeholder'));
        $js_msgTextConfirm.on('keydown', (e) => {
            if ((e.keyCode === 13) && (e.ctrlKey || e.metaKey)) {
                $('#js_messageSubmitButton').click();
            }
        });

        let autoText = getItem('ikaeasy-msg-to-send');
        if (autoText) {
            $js_msgTextConfirm.val(autoText);
            removeItem('ikaeasy-msg-to-send');
        }

        $js_msgTextConfirm.focus();
    }

    initBackspace() {

    }

}

export default Page;
