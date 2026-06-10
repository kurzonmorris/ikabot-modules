import Parent from './dummy.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle([$('#militaryAdvisor .premiumAccount').closest('.contentBox01h')]);
    }
}

export default Page;
