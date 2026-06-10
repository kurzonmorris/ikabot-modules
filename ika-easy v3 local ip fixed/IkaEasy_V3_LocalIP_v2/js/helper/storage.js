import Events from './event.js';
import { getThisKey } from '../utils.js';

class Storage extends Events {
    cache = {};
    constructor() {
        super();

        chrome.storage.onChanged.addListener((changes, namespace) => {
            _.each(changes, (val, key) => {
                if (val.oldValue) {
                    val.oldValue = JSON.parse(val.oldValue);
                }

                if (val.newValue) {
                    val.newValue = JSON.parse(val.newValue);
                }

                this.emit(key, [val, key, namespace]);
            });
        });
    }

    on(key, callback) {
        return super.on(getThisKey(key), callback);
    }

    off(key) {
        return super.off(getThisKey(key));
    }

    get(key) {
        return new Promise(resolve => {
            key = getThisKey(key);
            if(key in this.cache){
                return resolve(this.cache[key]);
            }

            chrome.storage.local.get(key, (result) => {
                let val = result[key] || null;
                if (val) {
                    val = JSON.parse(val);
                }
                this.cache[key] = val;
                resolve(val, null);
            });
        })
    }

    set(key, value) {
        return new Promise(resolve => {
            const _key = getThisKey(key);
            let data = {};
            data[_key] = JSON.stringify(value);

            try {
                chrome.storage.local.set(data, () => {
                    resolve(true);
                    this.cache[_key] = value;
                });
            } catch (e) {
                console.log(e, data);
                resolve(false);
            }
        })
    }
}

export default new Storage();
