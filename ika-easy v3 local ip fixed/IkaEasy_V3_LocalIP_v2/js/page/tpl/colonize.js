import Parent from './transport.js';

class Page extends Parent {

    init() {
        this.ikariamPremiumToggle(['#moveCity']);

        if ($('#createColony').data('updated')) {
            return;
        }

        $('#createColony').data('updated', true);
        this.moveTransportBtn($('#transport .resourceAssign'));
        this.updateMinMaxButtons($('#transport'));

        $('#transport').addClass('ikaeasy-transport-wrap');
        this.updateMovements();
    }
}

export default Page;
