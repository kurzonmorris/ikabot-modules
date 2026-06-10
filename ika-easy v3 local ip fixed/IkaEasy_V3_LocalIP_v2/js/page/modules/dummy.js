import Parent from '../common.js';

class Dummy extends Parent {
    constructor(parent) {
        super();

        this.parent = parent;
        this.init();
    }

    init() {

    }

    updated() {
        this.init();
    }

    refresh() {
        super.refresh();
    }

    destroy() {

    }
}

export default Dummy;
