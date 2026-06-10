import Parent from './diplomacyAdvisor.js';
import { getItem, removeItem } from '../../utils.js';

class Page extends Parent {

    async init() {
        await super.init();

        let $js_msgTextConfirm = $('#js_islandMsgTextInput');
        $js_msgTextConfirm.attr('placeholder', LANGUAGE.getLocalizedString('dummy_message_placeholder'));
        $js_msgTextConfirm.on('keydown', (e) => {
            if ((e.keyCode === 13) && (e.ctrlKey || e.metaKey)) {
                $('#tab_diplomacyIslandBoard input[type="submit"]').click();
            }
        });

        let autoText = getItem('ikaeasy-msg-to-send');
        if (autoText) {
            $js_msgTextConfirm.val(autoText);
            removeItem('ikaeasy-msg-to-send');
        }

        $js_msgTextConfirm.focus();
    }

}

export default Page;
