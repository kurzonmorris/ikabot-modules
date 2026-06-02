import options from './options.js';
import storage from './storage.js';
import Win from './win.js';
import Render from './templater.js';
import { delay } from '../utils.js';

class WhatNews {
    async init(version) {
        if (!version) {
            return;
        }

        const v = await storage.get('whatnews_version');
        if (v === version) {
            return;
        }

        await delay(3000);
        const response = await fetch(chrome.runtime.getURL('/whatnews.json'));
        const res = await response.json();

        if ((res.enabled) && (res.version) && (res.version === version)) {
            let win = new Win({
                title: LANGUAGE.getLocalizedString('whatnews.title')
            });

            win.on('ready', async () => {
                try {
                    const lang = (currentLanguage === 'ru') ? 'ru' : 'en';
                    const html = await Render('whatNews', {
                        text:       res.text[lang],
                        new:        res.new[lang],
                        fixed:      res.fixed[lang],
                        hotfix:     res.hotfix[lang],
                        newOptions: res.options,
                        options:    options,
                        lang:       lang
                    });

                    win.getContent().html(html);
                    win.setToCenter();
                    win.$el.addClass('ikaeasy-whatnews');

                    win.$el.find('input[type="checkbox"]').change((e) => {
                        let $input = $(e.currentTarget);
                        let name = $input.attr('name');

                        options.set(name, $input.prop('checked'));
                    });

                    win.$el.find('.donate-copy').click((e) => {
                        const $el = $(e.currentTarget);
                        const text = $el.data('clipboard-text');

                        // copy to clipboard
                        navigator.clipboard.writeText(text);
                    });
                } catch(e) {
                    console.error(e);
                    win && win.remove();
                }

                storage.set('whatnews_version', version);
            });
        } else {
            storage.set('whatnews_version', version);
        }
    }
}

export default new WhatNews();
