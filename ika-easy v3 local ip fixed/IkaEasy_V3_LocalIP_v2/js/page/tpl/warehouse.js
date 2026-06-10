import Parent from './dummy.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle([$('.premiumOfferBox'), $('#js_safe_capacity_bonus.red').closest('tr'), $('#js_storage_capacity_bonus.red').closest('tr')]);
    }
}

export default Page;
