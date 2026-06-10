import Parent from './options.js';

class Page extends Parent {

    async init() {
        await super.init();
        this.$page = $('#tab_optionsAccount');
    }

}

export default Page;
