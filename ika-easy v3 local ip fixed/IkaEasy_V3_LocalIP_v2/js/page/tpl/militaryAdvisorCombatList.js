import Parent from './dummy.js';
import { getItem } from '../../utils.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle([$('#militaryAdvisor .premiumAccount').closest('.contentBox01h')]);

        this.addIkalogIcons();
    }

    addIkalogIcons() {
        if ($('#combatList').data('updated')) {
            return;
        }
        $('#combatList').data('updated', true);
        let battles = getItem('battles') || {};

        _.each($('#combatList tbody tr'), ($tr) => {
            $tr = $($tr);

            let rep_date = $tr.find('.date').text().trim();
            let $tdr = $tr.find('.subject');
            let rep_id = $tdr.attr('onclick').match(/combatId=([0-9]+)/)[1];
            $tdr.after(`<td class="subject">${$tr.find('.subject').html()}</td>`);
            $tdr.remove();
            let $td = $tr.find('.subject');

            if (battles[rep_id]) {
                let $a = $(`<a href="${battles[rep_id].ikalogs}" target="_blank" class="ikaeasy_icon_ikalogs"></a>`);
                if (rep_date !== battles[rep_id].date) {
                    $a.addClass('ikaeasy_icon_ikalogs_old');
                }

                $td.prepend($a);
            }
        });
    }
}

export default Page;
