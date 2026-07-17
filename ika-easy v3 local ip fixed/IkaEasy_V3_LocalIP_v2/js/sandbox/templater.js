import Options from './options.js';
import { secondToTime, transformHours } from '../utils.js';
import { Government, Buildings, Resources } from '../const.js';

class Templater {
    constructor() {
        this._compilled = {};
        this._loading = {};
        this._prefix = '/tpl/';
        this._revision = '1';
    }

    preload(path, callback) {
        this.prepare(path, callback);
    }

    prepare(path, callback) {
        if (this._compilled[path]) {
            callback && callback();
            return;
        }

        if (this._loading[path]) {
            callback && this._loading[path].push(callback);
            return;
        }

        this._loading[path] = [];
        callback && this._loading[path].push(callback);

        const url = [this._prefix, path, '?', this._revision].join('');
        console.log('URL:', url)
        getText(url, (text) => {
            this._compilled[path] = _.template(text);

            _.each(this._loading[path], (v) => {
                if (typeof v === 'function') {
                    v();
                }
            });

            delete this._loading[path];
        }, true);
    }

    render(path, data, callback) {
        if (!path.endsWith('.ejs')) {
            path += '.ejs';
        }

        if (typeof data === 'function') {
            callback = data;
            data = {};
        }

        if (!this._compilled[path]) {
            if (typeof callback === 'function') {
                let cb = () => {
                    this.render(path, data, callback);
                };

                if (this._loading[path]) {
                    this._loading[path].push(cb);
                } else {
                    this.prepare(path, cb);
                }

                return;
            }

            const url = [this._prefix, path, '?', this._revision].join('');
            getText(url, (text) => {
                this._compilled[path] = _.template(text, {sourceURL: url});
            }, false);
        }

        let _data = data || {};
        _data.options = Options;

        const isRussianSupported = !!navigator.languages.find(x => x === 'ru-RU' || x === 'ru');

        let q = {
            data    : _data,
            include : this.render.bind(this),
            lget    : LANGUAGE.getLocalizedString,
            num     : formatNumber,
            numK    : formatNumberK,
            isRussianSupported: isRussianSupported,
            url     : (url) => PARAMS.extUrl + url,
            options : Options,
            Utils: {
                secondToTime,
                handleSize,
                transformHours
            },

            Const: {
                Government,
                Buildings,
                Resources
            }
        };

        if (this._compilled[path]) {
            const result = this._compilled[path](q);
            if (typeof callback === 'function') {
                callback(result);
            }

            return result;
        }

        if (typeof this._compilled[path] !== 'undefined') {
            return this._compilled[path](q);
        }
    }
}

function handleSize(size) {
    if ((typeof size === 'number') || (/^([0-9]+)$/.test(size))) {
        return `${size}px`;
    }

    return size;
}

function formatNumber(number, def, forceShowSign = true, separators) {
    def = def || '0';
    let sep = (typeof PARAMS.Front !== 'undefined') ? { thousand: PARAMS.Front.data.localizationStrings.thousandSeperator, decimal: PARAMS.Front.data.localizationStrings.decimalPoint } : separators;
    let result = ((number || "") + "").replace(/(\d)(?=(?:\d{3})+(?:$|\.|,))/g, "$1" + sep.thousand).replace(".", sep.decimal);

    if ((forceShowSign) && (number > 0)) {
        result = `+${result}`;
    }

    return ((!result) || (result === '0')) ? def : result;
}

function formatNumberK(number, def, forceShowSign = false, separators) {
    let k = 0;
    while (number >= 100000) {
        number = Math.floor(number / 1000);
        k++;
    }

    def = def || '0';
    let sep = (typeof PARAMS.Front !== 'undefined') ? { thousand: PARAMS.Front.data.localizationStrings.thousandSeperator, decimal: PARAMS.Front.data.localizationStrings.decimalPoint } : separators;
    let result = ((number || "") + "").replace(/(\d)(?=(?:\d{3})+(?:$|\.|,))/g, "$1" + sep.thousand).replace(".", sep.decimal);

    if ((forceShowSign) && (number > 0)) {
        result = `+${result}`;
    }

    let response = ((!result) || (result === '0')) ? def : result;
    while (k-- > 0) {
        response += 'k';
    }

    return response;
}

function getText(url, callback, async) {
    return $.ajax({
        url      : url,
        async    : (typeof async === 'undefined') ? true : !!async,
        dataType : "text",
        error    : function(data, result){callback(data, result);},
        success  : function(data, result){callback(data, result);}
    });
}

export default new Templater();
