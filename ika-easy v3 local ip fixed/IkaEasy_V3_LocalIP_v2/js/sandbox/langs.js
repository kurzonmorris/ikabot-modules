import allLangs from '../../lang/all.js';
import assignedLng from '../assignedLangs.js';

export default function setLang(lang) {
    const _lang = assignedLng[lang] || 'en';
    window.LANGUAGE = allLangs.en;
    window.currentLanguage = 'en';

    if (_lang !== 'en') {
        let newLangList = allLangs[_lang];
        if (newLangList) {
            window.LANGUAGE = $.extend(true, LANGUAGE, newLangList);
            window.currentLanguage = _lang;
        }
    }

    moment.locale(_lang);
    _modifyLANGUAGES();
}

function _modifyLANGUAGES () {
    if (typeof LANGUAGE.getLocalizedString !== 'function') {
        LANGUAGE.getLocalizedString = function(key, params) {
            let str = LANGUAGE[key];
            if ((typeof str !== 'string') || (!str)) {
                return key;
            }

            return _LanguageUtils.applyVariables(str, params);
        };
    }
}

const _LanguageUtils = (function() {
    function getValue(obj, key, p, defValue) {
        let res = obj && obj[key];
        if (typeof res === 'function') {
            res = res.call(obj, p);
        }
        return (res == null) ? (defValue || null) : res;
    }

    return {
        applyVariables: function (str, params) {
            return str.replace(/\%(~?)(\w+)(?::(\w+))?\%/g, function(s, glob, key, p) {
                return getValue(params, key, p, s);
            });
        }
    };
})();
