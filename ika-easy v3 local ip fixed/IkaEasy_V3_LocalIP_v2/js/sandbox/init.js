import setLang from './langs.js';
import Templater from './templater.js';

setLang('en');

window.PARAMS = {};
window.addEventListener('message', function (event) {
    if ((event.data.type) && ((event.data.type === 'FROM_IKAEASY_V3'))) {
        if (event.data.cmd === 'sandbox') {
            if (event.data.subType === 'tpl_render') {
                let result = Templater.render(event.data.data.path, event.data.data.params);
                event.source.postMessage({ type: 'FROM_IKAEASY_V3', cmd: 'sandbox', subType: 'tpl_ready', form: {key: event.data.data.key, result: result, path: event.data.data.path} }, event.origin);
            } else if (event.data.subType === 'set_params') {

                PARAMS = _.merge(PARAMS, event.data.data);
                setLang(PARAMS.lang);
            }
        }
    }
});
