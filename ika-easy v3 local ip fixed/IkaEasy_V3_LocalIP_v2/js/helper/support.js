import Win from './win.js';
import {execute_js} from '../utils.js';
import Render from './templater.js';

class Support {

    show() {
        if ($('#ikaeasy-support-win').length) {
            return;
        }

        let win = new Win({
            title: 'IkaEasy',
            subtitle: LANGUAGE.getLocalizedString('support_text'),
        });

        win.on('ready', async () => {
            const tpl = await Render('helper-support', {});
            this.$el = $(tpl);
            win.getContent().empty().append(this.$el);

            let $textarea = this.$el.find('textarea');
            this.$el.keydown((e) => {
                if ([8, 37, 38, 39, 40, 46].indexOf(e.keyCode) > -1) {
                    return true;
                }

                let len = $textarea.val().length;
                if (len >= 4000) {
                    e.preventDefault();
                }
            });

            let $issueType = this.$el.find('select[name="issueType"]');
            const $agreeCheck = this.$el.find('input[name="agreeCheck"]');

            $issueType.change(() => {
                let isBug = $issueType.val() === 'Bug';
                this.$el.find('input[name="issueDetails"]').closest('div').toggleClass('ikaeasy-disabled-div', !isBug);
                $('#issueDetails').prop('checked', isBug).prop('disabled', !isBug);

                if (!isBug) {
                    this.$el.find('button').prop('disabled', false);
                    $agreeCheck.closest('div').prop('hidden', true);
                } else {
                    $agreeCheck.closest('div').prop('hidden', false);
                    this.$el.find('button').prop('disabled', !$agreeCheck.prop('checked'));
                }
            });

            $agreeCheck.change(() => {
                if ($agreeCheck.prop('checked')) {
                    this.$el.find('button').prop('disabled', false);
                } else {
                    this.$el.find('button').prop('disabled', true);
                }
            });

            this.$el.find('button').click(async (e) => {
                e.preventDefault();
                let $input = this.$el.find('input[name="issueTitle"]');
                let type = $issueType.val();
                let title = $input.val().trim();
                let email = this.$el.find('input[name="issueEmail"]').val().trim();
                let desc = $textarea.val().trim();
                let details = ((type === 'Bug') && (this.$el.find('input[name="issueDetails"]').prop('checked')));

                if ((!email) && (type === 'Bug')) {
                    if (!confirm(LANGUAGE.getLocalizedString('support_confirm_email'))) {
                        return;
                    }
                }

                if (!title) {
                    $input.focus();
                    return;
                }

                if (!desc) {
                    $textarea.focus();
                    return;
                }

                const  cityId = (Front.data.city) ? Front.data.city.id : Front.data.cities.selectedCityId;
                const body = await Render('helper-support-email', {text: desc, cityId: cityId, email: email, details: details});

                win.remove();
                chrome.runtime.sendMessage({ cmd: 'ajax', url: 'default/index/ikaeasy', method: 'post', body: {title: title, desc: body, type: type} }, (data) => {
                    execute_js(`BubbleTips.bindBubbleTip(1, 10, "${LANGUAGE.getLocalizedString('support_sent_success')}")`);
                });
            });
        });
    }
}

export default new Support();
