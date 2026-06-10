import Parent from './_dummy.js';

class PrepareTpl extends Parent {
    constructor(data) {
        super(data);
    }

    getData() {
        if (this._data.newOptions.length) {
            this._data._newOptionsData = {};

            _.each(this._data.newOptions, (option, key) => {
                this._data._newOptionsData[key] = {
                    option: this._data.options.get(option),
                    hasHint: this._data.options.hasHint(option)
                };
            });
        }

        return this._data;
    }
}

export default PrepareTpl;