import { Government } from '../const.js';

class Info {
    constructor() {
        this._data = {
            government: Government.IKACRACY
        };
    }

    updateData(data) {
        this._data = data || {
            government: Government.IKACRACY
        };
    }

    set(key, value) {
        if (this._data[key] !== value) {
            this._data[key] = value;
            Front.ikaeasyData.save();
        }
    }

    get(key) {
        return this._data[key] || null;
    }
}

export default new Info();
