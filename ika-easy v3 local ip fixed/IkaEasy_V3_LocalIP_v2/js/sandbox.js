import Event from './helper/event.js';
import { getServerDomain, getServerWorld} from './utils.js';

class Sandbox extends Event {
    constructor() {
        super();

        this._sandbox = document.createElement('iframe');
        this._sandbox.onload = this.scriptLoaded.bind(this);
        this._sandbox.id = 'sandbox';
        this._sandbox.style.cssText = 'display:none;';
        this._sandbox.src = chrome.runtime.getURL('sandbox.html');
        document.body.appendChild(this._sandbox);

        window.addEventListener('message', (event) => {
            if ((event.data.type) && ((event.data.type === 'FROM_IKAEASY_V3'))) {
                if (event.data.cmd === 'sandbox') {
                    this.emit('message', [event]);
                }
            }
        });
    }

    scriptLoaded() {
        let extUrl = chrome.runtime.getURL('');
        extUrl = extUrl.endsWith('/') ? extUrl.slice(0,-1) : extUrl;

        this.send('set_params', {
            lang: getServerDomain(),
            serverWorld: getServerWorld(),
            extUrl: extUrl
        });

        this.emit('ready');
    }

    send(subType, data) {
        this._sandbox.contentWindow.postMessage({
            type: 'FROM_IKAEASY_V3',
            cmd: 'sandbox',
            subType: subType,
            data: data
        }, '*');
    }
}

export default new Sandbox();
