'use strict';

import Parent from './dummy.js';

class Module extends Parent {
    constructor(parent) {
        super(parent, 'espionage.ejs')
    }

    init() {

    }
}

export default Module;
