import Parent from './dummy.js';
import { getInt } from '../../utils.js';

class Page extends Parent {

    init() {
        let priests = getInt($('#inputPriests').val());
        this._city.set('priests', priests);
    }

}

export default Page;
