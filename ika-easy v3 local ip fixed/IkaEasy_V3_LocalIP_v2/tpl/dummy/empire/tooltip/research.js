import Parent from '../../../_dummy.js';

class PrepareTpl extends Parent {
    constructor(data) {
        super(data);
    }

    getData() {
        this._data.city._research = this._data.city.getResearch();
        this._data.city._scientists = this._data.city.get('scientists');
        this._data.city._researchMultiplier = this._data.city.getResearchMultiplier();
        this._data.city._researchGovernmentMultiplier = this._data.city.getResearchGovernmentMultiplier();
        this._data.city._corruption = this._data.city.getCorruption();

        return this._data;
    }
}

export default PrepareTpl;
