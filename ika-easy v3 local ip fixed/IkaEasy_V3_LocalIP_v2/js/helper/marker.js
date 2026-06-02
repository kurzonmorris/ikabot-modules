import {getItem, setItem} from '../utils.js';

class Marker {
    constructor() {
        this.colors = Object.freeze(['orange', 'bordo', 'pink', 'purple', 'turquoise', 'black', 'white']);
        this.name = getItem('marker') || {};
    }

    setColor(name, color, ally = false) {
        name = name.trim();
        name += ally ? '_ally' : '';

        if (this.colors.indexOf(color) === -1) {
            console.error('Unknown color', color);
            return;
        }
        _.each(this.name, (v, color) => {
            if (v.indexOf(name) > -1) {
                this.name[color] = _.without(v, name);
            }
        });

        if (typeof this.name[color] === 'undefined') {
            this.name[color] = [];
        }

        this.name[color].push(name);
        setItem('marker', this.name);
    }

    deleteName(name) {
        name = name.trim();

        _.each(this.name, (v, color) => {
            this.name[color] = _.filter(v, (a) => {
                return a.trim() !== name;
            });
        });
        setItem('marker', this.name);

        return true;
    }
}

export default new Marker();
