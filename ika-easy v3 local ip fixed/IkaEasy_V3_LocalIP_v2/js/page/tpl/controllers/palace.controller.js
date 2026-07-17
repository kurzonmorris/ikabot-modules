import ControllerBase from '../../controller.js';

class palaceController extends ControllerBase{
    init(){
        // Обновляем форму правления
        this._ieData.info.set('government', this.DOMselect('.government_pic img').attr('src').slice(16, -8));
    }
}
export default new palaceController();
