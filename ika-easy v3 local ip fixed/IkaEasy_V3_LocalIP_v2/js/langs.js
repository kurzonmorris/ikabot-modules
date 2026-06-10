import { getServerDomain, _modifyLANGUAGES } from './utils.js';
import allLangs from '../lang/all.js';
import assignedLng from './assignedLangs.js';

let lang = assignedLng[getServerDomain()] || 'en';
window.currentLanguage = 'en';
window.LANGUAGE = allLangs.en;

if (lang !== 'en') {
    let newLangList = allLangs[lang];
    if (newLangList) {
        LANGUAGE = $.extend(true, LANGUAGE, newLangList);
        currentLanguage = lang;
    }
}

moment.locale(currentLanguage);
_modifyLANGUAGES();
