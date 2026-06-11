
class PrepareTpl {
    constructor(data, helpers = {}) {
        this._data = data;
        this._helpers = helpers;
    }

    getData() {
        return this._data;
    }
    getHelpers() {
        return this._helpers;
    }
}

export default PrepareTpl;
