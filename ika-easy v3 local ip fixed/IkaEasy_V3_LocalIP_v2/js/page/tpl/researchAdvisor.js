import Parent from './dummy.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle([$('#researchAdvisor .premiumAccount').closest('.contentBox01h'), $('#researchAdvisor .premium_research_link')]);
        this._ieData.getResearch().update(this._data.templateView.params.currResearchType);
    }
}

export default Page;
