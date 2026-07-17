import Parent from './options.js';

class Page extends Parent {

    async init() {
        await super.init();
        this.$page = $('#tab_optionsNotification');
    }

}

export default Page;
