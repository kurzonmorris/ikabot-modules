import allLangs from '../../lang/all.js';

export default function getLangs(lang) {
    let Language = _.clone(allLangs['en']);

    if (lang !== 'en') {
        let newLang = allLangs[lang];
        if (newLang) {
            Language = $.extend(true, Language, newLang);
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

    Language.getLocalizedString = function(key, params) {
        let str = Language[key];
        if ((typeof str !== 'string') || (!str)) {
            return key;
        }

        return _LanguageUtils.applyVariables(str, params);
    };

    return Language;
}
