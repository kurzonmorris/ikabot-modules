import CacheService from '../../helper/cache.js';
import IkalogsRu from '../../helper/ikalogsRu.js';
import Parent from './dummy.js';

class Page extends Parent {

    async init(){
        this.cache = CacheService.getModuleCache('diplomacyAdvisor');
        this.lazyload = [];
        this.$parent = $(document.getElementById("tab_diplomacyAdvisor"));
        if (this.options.get('diplomacy_links', true)) {
            await this.makeActiveLinks()
        }

        if (this.options.get('diplomacy_tab_members', true)) {
            await this.addMembersTab();
            this.changeTabsText();
        }

        this.ikariamPremiumToggle([$('.templateView .premiumAccount').closest('.contentBox01h'), $('.templateView .ambrosia, .templateView .chargeAmbrosia')]);
    }

    //Изменение названий закладок
    changeTabsText() {
        let tabs = {
            '.tab_diplomacyAdvisor'     : LANGUAGE.getLocalizedString('diplomacy_message'),
            '.tab_diplomacyIslandBoard' : LANGUAGE.getLocalizedString('diplomacy_agora'),
            '.tab_diplomacyTreaty'      : LANGUAGE.getLocalizedString('diplomacy_treaty'),
            '.tab_diplomacyAlly'        : LANGUAGE.getLocalizedString('diplomacy_alliance')
        };

        _.each(tabs, (text, selector) => {
            let $el = $(selector);
            let m = $el.text().match(/(\(\d+\))/);

            if (m && m.length >= 2) {
                text += ` ${m[1]}`;
            }

            $el.text(text);
        });
    }

    //Добавление вкладки со списком игроков альянса
    async addMembersTab() {
        let $tab = $('#js_tab_diplomacyMembers');

        if (!$tab.length) {
            const tpl = await this.render('diplomacy-members');
            $tab = $(tpl);
            $('#js_tab_diplomacyAlly').after($tab);
        }

        if (($('#diplomacyAllyMemberlist').length > 0) && $(".filter.diplomacy .filterEntry:first-child:not(.active)")) {
            $tab.addClass('selected').siblings().removeClass('selected');
        }
    }

    async checkIfCached(key, action){
        if(!(key in this.cache)){
            this.cache[key] = await action();
        }
        return this.cache[key];
    }


    //Создание активных ссылок
    async makeActiveLinks() {
        const $messages = this.$parent.find('#deleteMessages .table01');
        const regExpUrl = /(?<!.=["'])https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{2,256}\.[a-z]{2,6}\b([-a-zA-Z0-9@:%_+.~#?&\/=]*)/g;
        const html = $messages.html().replace(regExpUrl, (url) => {
            url = url.toLocaleString();

            // Картинки показываем сразу
            if (/\.(jpe?g|gif|png)$/.test(url)) {
                // TODO: ?
                return this.checkIfCached(url, async () => {
                    return await this.render('diplomacy-link-image', {url: url, img: url});
                });
            }

            // clip2net - подгружаем и показываем
            if (/https?:\/\/clip2net\.com/.test(url)) {
                if(url in this.cache){
                    return `<div class="ikaeasy_cli2pnet_replace ikaeasy_replace_done">${this.cache[url]}</div>`
                }
                this.lazyload.push(()=>this.setClip2net(url))
                return `<div class="ikaeasy_cli2pnet_replace" data-url="${url}">${url}</div>`
            }

            // ikalogs
            if (/https?:\/\/ikalogs.ru\/report\/.*/.test(url)) {
                const [, , , , battleId] = url.split('/');
                this.lazyload.push(()=>this.ikaLogsReport(battleId, url))
                return `<div class="ikaeasy_ikalogs_replace" data-url="${url}">${url}</div>`
            }

            // Просто ссылка
            return `<a href="${url}" target="_blank" class="externalURL">${_.escape(url)}</a>`;
        });

        // bug: we need to find element again, as until we get here sometimes
        // ikariam updating the dom, so we are not replacing current element, but cached...
        this.$parent.find('#deleteMessages .table01').html(html);
        this.initLazyLoad();

    }

    initLazyLoad(){
        for(const fn of this.lazyload){
            fn();
        }
    }

    async getClip2net(url){
        return new Promise(resolve => {
            chrome.runtime.sendMessage({cmd: 'ajax_html', url: url}, (res) => resolve(res));
        });
    }

    async setClip2net(url){
        if(!(url in this.cache)){
            const res = await this.getClip2net(url);
            const href = '//clip2net.com/'+$(res).find('div.image-pic img').attr('src').replace(/^\//, '');
            this.cache[url] = await this.render('diplomacy-link-image', {url: url, img: href});
        }

        $(`.ikaeasy_cli2pnet_replace[data-url="${url}"]:not(.ikaeasy_replace_done)`).html(this.cache[url]).addClass("ikaeasy_replace_done");
    }

    async ikaLogsReport(battleId, url){
        if(!(url in this.cache)){
            this.cache[url] = 'loading';
            const { report, users, summary } = await IkalogsRu.getBattleInfo(battleId);
            this.cache[url] = await this.render('diplomacy-ikalogs', {url: url, report, users, summary});
        }

        if(this.cache[url] === 'loading'){
            return;
        }
        $(`.ikaeasy_ikalogs_replace[data-url="${url}"]:not(.ikaeasy_replace_done)`).html(this.cache[url]).addClass("ikaeasy_replace_done");
    }
}

export default Page;
