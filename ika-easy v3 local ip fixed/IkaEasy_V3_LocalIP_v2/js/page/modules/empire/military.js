'use strict';

import Parent from './dummy.js';

class Module extends Parent {
    constructor(parent) {
        super(parent, 'military.ejs')
    }

    init() {

    }
}

export default Module;
