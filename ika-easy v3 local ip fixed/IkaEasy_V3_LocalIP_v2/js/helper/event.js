const SPACE = ' ';
const DOT = '.';
const EMPTY_STRING = '';
class EventEmitter {
    constructor() {
        this.handlers = {};
    }

    on(evtStr, callback) {
        const events = evtStr.split(SPACE);
        _.each(events, (event) => {
            let parts = event.split(DOT);
            let baseEvent = parts[0];
            let name = parts[1] || EMPTY_STRING;

            if (!this.handlers[baseEvent]) {
                this.handlers[baseEvent] = [];
            }

            this.handlers[baseEvent].push({
                name: name,
                callback: callback
            });
        });

        return true;
    }

    off(evtStr) {
        if (!evtStr) {
            return;
        }

        const events = evtStr.split(SPACE);
        _.each(events, (event) => {
            let parts = event.split(DOT);
            let baseEvent = parts[0];
            let name = parts[1] || EMPTY_STRING;

            if (baseEvent) {
                this._off(baseEvent, name);
            } else {
                for (let t in this.handlers) {
                    this._off(t, name);
                }
            }
        });
    }

    _off(baseEvent, name) {
        let handlers = this.handlers[baseEvent] || [];
        let len = handlers.length;

        for(let i = 0; i < handlers.length; i++) {
            let event = handlers[i];
            if ((!name) || (event.name === name)) {
                handlers.splice(i, 1);

                if (handlers.length === 0) {
                    delete this.handlers[baseEvent];
                    break;
                }

                i--;
            }
        }
    }

    removeAllListeners(event) {
        this.handlers[event] = [];
        return true;
    }

    resetAllListeners() {
        this.handlers = [];
    }

    emit(event, args = []) {
        if (!this.handlers[event]) {
            return;
        }

        _.each(this.handlers[event], (ev) => {
            if (ev) {
                ev.callback.apply(this, args);
            }
        });
    }
}

export default EventEmitter;
