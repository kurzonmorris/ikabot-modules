import Parent from './resource.js';

class Page extends Parent {

    init() {
        super.init();
        this.ikariamPremiumToggle([$('#tradegood .premiumOfferBox')]);
    }

}

export default Page;
