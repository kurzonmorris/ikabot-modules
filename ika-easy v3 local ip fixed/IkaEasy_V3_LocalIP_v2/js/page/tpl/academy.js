import Parent from './dummy.js';
import { getFloat } from '../../utils.js';

class Page extends Parent {

    init() {
        this._city.set('scientists', ~~getFloat($('#js_academy_research_tooltip_basic_production').text()) );
    }

}

export default Page;
