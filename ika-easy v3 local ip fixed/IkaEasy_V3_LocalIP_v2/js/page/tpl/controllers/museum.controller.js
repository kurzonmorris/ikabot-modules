import ControllerBase from '../../controller.js';import { getInt } from '../../../utils.js';

class museumController extends ControllerBase{

    init() {
        // get from '<span>..</spaan> 10/11' => 10
        const culturalGoods = getInt(this.DOMselect('#tab_museum .content .goods .headline_huge.value')
            .clone()    //clone the element
            .children() //select all the children
            .remove()   //remove all the children
            .end()  //again go back to selected element
            .text()
            .trim()
            .split("/")[0]);

        this._city.set('culturalGoods', culturalGoods);
    }

}

export default new museumController();
