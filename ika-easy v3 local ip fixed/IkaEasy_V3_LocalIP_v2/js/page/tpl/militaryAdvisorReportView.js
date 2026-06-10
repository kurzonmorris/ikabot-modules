import Ikalogs from '../../../js/helper/ikalogs.js';
import Parent from './dummy.js';

class Page extends Parent {

    init() {
        if ($('#ikalogs_saver').length) {
            return;
        }

        if ($('#troopsReport .attacker').length) {
            this.ikalogs = new Ikalogs(this._data.avatarId);
        }

        this.ikariamPremiumToggle([$('#militaryAdvisor .premiumAccount').closest('.contentBox01h')]);
        this.initChecker();
    }

    updated() {

    }

    initChecker() {
        if (this.interval) {
            return;
        }

        let cnt = 0;
        this.interval = setInterval(() => {
            this.init();

            if (++cnt > 5) {
                clearInterval(this.interval);
            }
        }, 100);
    }

    destroy() {
        clearInterval(this.interval);

        if ($('#ikalogs_saver').length) {
            $('#ikalogs_saver').closest('ul').remove();
        }
    }
}

export default Page;
