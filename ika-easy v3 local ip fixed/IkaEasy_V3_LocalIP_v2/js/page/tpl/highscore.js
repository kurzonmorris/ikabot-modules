import Marker from '../../helper/marker.js';
import Parent from './dummy.js';

class Page extends Parent {

    init() {
        $('#tab_highscore input[name="searchUser"]').focus();
        this.marker();
    }

    marker() {
        function refreshTop() {
            $('.centerButton .button').click();
        }

        _.each($('.highscore tr'), ($tr) => {
            $tr = $($tr);
            if ($tr.data('updated')) {
                return;
            }

            $tr.data('updated', true);
            if (($tr.hasClass('own')) || ($tr.hasClass('ownally'))) {
                return;
            }



            let $markerMenu = $('<div class="ikaeasy_top_marker_menu" title=""></div>');
            $tr.append($markerMenu);

            let colors = ['blank'].concat(Marker.colors);
            _.each(colors, (color) => {
                let $menuCell = $(`<div class="ikaeasy_top_menuCell" data-color="${ color }" title=""></div>`);
                $markerMenu.append($menuCell);

                $menuCell.click(() => {
                    let $ally = $('.ikaeasy_marker_big', $tr);

                    if (color === 'blank') {
                        Marker.deleteName($ally.data('name'));
                    } else {
                        Marker.setColor($ally.data('name'), color);
                    }

                    $markerMenu.removeClass('show');
                    setTimeout(refreshTop, 500);
                });
            });


            const allyTag = $('.allytag a', $tr).text().trim();
            const userName = $('.name a', $tr).text().trim();
            // if (!allyTag) {
            //     return;
            // }

            const $marker = $(`<div class="ikaeasy_marker_big" data-ally="${ allyTag }" data-name="${ userName }"></div>`);
            $('.action a', $tr).before($marker);
            $marker.off('click').on('click', () => {
                $('.ikaeasy_top_marker_menu', $tr).toggleClass('show');
            });

            for(const color in Marker.name){
                const list = Marker.name[color];
                if(list.indexOf(userName) > -1){
                    $tr.addClass(`marker_top_${color}`);
                    return true;
                }else if(list.indexOf(allyTag+'_ally') > -1){
                    $tr.addClass(`marker_top_${color}`);
                    return true;
                }
            }
        });
    }

}

export default Page;
