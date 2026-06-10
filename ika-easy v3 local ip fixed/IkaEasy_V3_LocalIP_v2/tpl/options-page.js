import Parent from './_dummy.js';

class PrepareTpl extends Parent {
    constructor(data) {
        super(data);
    }

    getData() {
        this._data._listData = {};
        _.each(this._data.list, (list, tabName) => {
            this._data._listData[tabName] = {};

            _.each(list, (option, key) => {
                this._data._listData[tabName][key] = {
                    option: this._data.options.get(option),
                    hasHint: this._data.options.hasHint(option)
                };
            });
        });

        return this._data;
    }
}

export default PrepareTpl;