import Parent from './dummy.js';

const TAVERN_INTERVAL_KEY = 'ikaeasy_tavern_interval';

class Page extends Parent {

    async init() {
        await this.addTavernSection();
    }

    async addTavernSection() {
        if ($('#ikaeasy_tavern_mgr').length) {
            return;
        }

        // Only show if playing through the ikabot web server.
        let tavernAvailable = false;
        try {
            const r = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=tavern_status`,
                { credentials: 'include', signal: AbortSignal.timeout(2000) }
            );
            if (r.ok) {
                const data = await r.json();
                tavernAvailable = !!data.available;
            }
        } catch (_) {}

        const savedInterval = parseInt(localStorage.getItem(TAVERN_INTERVAL_KEY)) || 24;
        const tpl = await this.render('tavern-mgr', { tavernAvailable, savedInterval });
        const $section = $(tpl);
        $section.attr('id', 'ikaeasy_tavern_mgr');

        // Append after the main tavern content box.
        const $wrap = $('.buildingLevel, #buildingPositionDiv, .contentBox01h').last();
        if ($wrap.length) {
            $wrap.after($section);
        } else {
            $('body').append($section);
        }

        if (tavernAvailable) {
            this._bindEvents($section);
        }
    }

    _bindEvents($section) {
        // Mode switches — only one active at a time.
        $section.on('click', '.ikaeasy-tavern-mode-btn', function () {
            $section.find('.ikaeasy-tavern-mode-btn').removeClass('ikaeasy-tavern-active');
            $(this).addClass('ikaeasy-tavern-active');

            const mode = $(this).data('mode');
            // Show slider row only for set_pct mode.
            $('#ikaeasy_tavern_slider_row', $section).toggle(mode === 'set_pct');

            const descs = {
                set_pct:     'Sets all taverns to the percentage shown on the slider.',
                zero:        'Sets all taverns to 0% — stops all wine consumption.',
                max:         'Sets all taverns to 100% — maximises citizen growth.',
                equilibrium: 'Runs at 100% until max citizens is reached, then drops to the lowest wine level that keeps satisfaction at least 5 above 0.',
            };
            $('#ikaeasy_tavern_mode_desc', $section).text(descs[mode] || '');
        });

        // Slider label sync.
        $section.on('input', '#ikaeasy_tavern_slider', function () {
            $('#ikaeasy_tavern_pct_label', $section).text($(this).val() + '%');
        });

        // Interval save.
        $section.on('change', '#ikaeasy_tavern_interval', function () {
            localStorage.setItem(TAVERN_INTERVAL_KEY, $(this).val());
        });

        // Apply button.
        $section.on('click', '#ikaeasy_tavern_apply', (e) => {
            e.preventDefault();
            this._applyMode($section);
        });
    }

    async _applyMode($section) {
        const $status  = $('#ikaeasy_tavern_status', $section);
        const $active  = $('.ikaeasy-tavern-mode-btn.ikaeasy-tavern-active', $section);

        if (!$active.length) {
            $status.css('color', 'red').text('Select a mode first.');
            return;
        }

        let mode       = $active.data('mode');
        let pct        = parseInt($('#ikaeasy_tavern_slider', $section).val()) || 0;
        const interval = parseInt($('#ikaeasy_tavern_interval', $section).val()) || 24;

        // Convenience modes map to set_pct with a fixed value.
        if (mode === 'zero') { mode = 'set_pct'; pct = 0; }
        if (mode === 'max')  { mode = 'set_pct'; pct = 100; }

        localStorage.setItem(TAVERN_INTERVAL_KEY, interval);

        $status.css('color', '').text('Applying…');
        $('#ikaeasy_tavern_apply', $section).prop('disabled', true);

        try {
            const resp = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy`,
                {
                    method:      'POST',
                    credentials: 'include',
                    headers:     { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ikaeasy_action: 'tavern_apply',
                        mode:           mode,
                        pct:            pct,
                        interval_hrs:   interval,
                    }),
                }
            );
            const data = await resp.json();
            if (data.ok) {
                const count = (data.results || []).length;
                $status.css('color', 'green').text(
                    mode === 'set_pct'
                        ? `Done — set ${count} tavern(s) to ${pct}%.`
                        : `Done — equilibrium check ran on ${count} city(s).`
                );
            } else {
                $status.css('color', 'red').text(data.error || 'Failed.');
            }
        } catch (_) {
            $status.css('color', 'red').text('Could not reach ikabot.');
        } finally {
            $('#ikaeasy_tavern_apply', $section).prop('disabled', false);
        }
    }
}

export default Page;
