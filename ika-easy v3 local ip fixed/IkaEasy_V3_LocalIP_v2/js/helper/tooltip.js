class Tooltip {
    constructor() {
        this.$tooltip = $('<div id="ikaeasy-tooltip"></div>');
        $('body').append(this.$tooltip);
    }

    show(e, $el, html) {
        $el.off('.tooltip');
        $el.on('mouseleave.tooltip', (e) => {
            this.hide();
        });
        $el.on('mousemove.tooltip', (e) => {
            this._updatePosition(e);
        });

        this.$tooltip.show().html( html );
        this._updatePosition(e);
    }

    hide() {
        this.$tooltip.empty().hide();
    }

    _updatePosition(e) {
        let pos = {
            top: e.pageY + 15,
            left: e.pageX + 15
        };

        this.$tooltip.css(pos);


        let rect = this.$tooltip[0].getBoundingClientRect();
        if (rect.bottom > window.innerHeight) {
            pos.top = e.pageY - rect.height - 10;
            this.$tooltip.css(pos);
        }

        if (rect.right > window.innerWidth) {
            pos.left = e.pageX - rect.width - 10;
            this.$tooltip.css(pos);
        }
    }
}

export default new Tooltip();
