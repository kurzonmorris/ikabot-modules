import Marker from '../../helper/marker.js';
import Parent from './dummy.js';

class Page extends Parent {
    async init() {
        await this.addMarker('ally');
        await this.addMarker('player');
    }

    async addMarker(type = 'ally') {
        $(`#${type}MarkerMenu`).remove();
        $(`#ikaeasy_${type}_marker_btn`).remove();
        $('body').off('.page');

        if (type === 'ally' && $('#js_selectedCityAllyName').text() === '-') {
            return;
        }

        let targetName;
        let btnTarget;

        switch(type) {
            case 'ally':
                targetName = '#js_selectedCityAllyName';
                btnTarget = '#js_selectedCityAlly';
                break;
            case 'player':
                targetName = '#js_selectedCityOwnerName';
                btnTarget = '.owner.alt';
                break;
        }

        const tpl = await this.render('marker', {id: type});
        const $markerMenu = $(tpl);

        $('body').append($markerMenu);

        let self = this;
        const cityColors = Marker.colors.map((v) => { return `marker_${v}`; });

        for(let i = 0; i < cityColors.length; i++) {
            const mcolor = cityColors[i];

            const color = mcolor.replace('marker_', '');
            const $city = $(`<div class="ikaeasy_marker_ally_list"><div class="cityBox ${mcolor}" id="${mcolor}" /></div>`);
            $markerMenu.append($city);

            $city.find('.cityBox').click(function () {
                Marker.setColor($(targetName).text(), color, (type === 'ally'));
                Front.page_bg.colorizeIt(type);
                $markerMenu.removeClass('show');
                self.addMarker(type);
            });

            const allysByColor = Marker.name[color];
            if (allysByColor) {
                for (let j = 0; j < allysByColor.length; j++) {
                    let ally = allysByColor[j];

                    let $delP;
                    if (type === 'ally' && ally.endsWith('_ally')) {
                        ally = ally.replace('_ally', '');
                        $delP = $(`<p class="markerAllys">${ally}</p>`);
                        $delP.click(function () {
                            Marker.deleteName(`${ally}_ally`, true);
                            $(this).remove();
                            Front.page_bg.colorizeIt('ally', $('#js_selectedCityAllyName').text());
                            self.addMarker();
                        });

                        $city.append($delP);
                    }

                    if (type === 'player' && !ally.endsWith('_ally')) {
                        $delP = $(`<p class="markerAllys">${ally}</p>`);
                        $delP.click(function (e) {
                            Marker.deleteName(ally);
                            $(this).remove();
                            Front.page_bg.colorizeIt('player', e.target.textContent);
                            self.addMarker(type);
                        });

                        $city.append($delP);
                    }
                }
            }
        }

        let $markerBtn = $(`<span id="ikaeasy_${type}_marker_btn"></span>`);
        $(btnTarget).find('.icons').append($markerBtn);

        $markerBtn.click(function() {
            const playerMenu = $('#playerMarkerMenu');
            const allyMenu = $('#allyMarkerMenu');

            if (!$markerMenu.hasClass('show')) {
                if (playerMenu) {
                    playerMenu.removeClass('show');
                }
                if (allyMenu) {
                    allyMenu.removeClass('show');
                }
                let position = $('#sidebar').position();
                $markerMenu.addClass('show').offset({left: position.left + 230, top: position.top + 19});
            } else {
                $markerMenu.removeClass('show');
            }
        });

        $(document).on('keyup.page', function(e) {
            if (e.keyCode === 27) {
                $markerMenu.removeClass('show');
            }
        });

        $('body').on('mouseup.page', function(e) {
            const type = e.target.id.split('_')[1];
            const $menu = $(`#${type}MarkerMenu`);
            if ([`ikaeasy_${type}_marker_btn`, `${type}MarkerMenu`].indexOf(e.target.id) === -1) {
                $menu.removeClass('show');
                return;
                $('body').off('mouseup.page');
            }
        });
    }

    destroy() {
        $('#allyMarkerMenu').remove();
        $('#playerMarkerMenu').remove();
        $('#ikaeasy_ally_marker_btn').remove();
        $('#ikaeasy_user_marker_btn').remove();
        $('body').off('.page');
        $(document).off('.page');
    }
}

export default Page;
