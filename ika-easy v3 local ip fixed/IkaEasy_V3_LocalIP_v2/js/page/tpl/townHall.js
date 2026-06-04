import Parent from './dummy.js';

const PROD_SCOPE_KEY    = 'ikaeasy_prod_scope';     // 'all' | 'city'
const PROD_TYPES_KEY    = 'ikaeasy_prod_types';     // 'wood' | 'luxury' | 'both'
const PROD_WOOD_PCT_KEY = 'ikaeasy_prod_wood_pct';
const PROD_LUX_PCT_KEY  = 'ikaeasy_prod_lux_pct';
const PROD_MAX_KEY      = 'ikaeasy_prod_maximise';

class Page extends Parent {

    async init() {
        this.ikariamPremiumToggle([$('#townHall .premiumOffer')]);
        await this.addProductionSection();
    }

    async addProductionSection() {
        if ($('#ikaeasy_prod_wrap').length) {
            return;
        }

        // Only show when playing through the ikabot proxy.
        let ikabotDetected = false;
        try {
            const r = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=cities`,
                { credentials: 'include', signal: AbortSignal.timeout(2000) }
            );
            if (r.ok) ikabotDetected = true;
        } catch (_) {}

        const saved = {
            scope:    localStorage.getItem(PROD_SCOPE_KEY)    || 'city',
            types:    localStorage.getItem(PROD_TYPES_KEY)    || 'both',
            woodPct:  parseInt(localStorage.getItem(PROD_WOOD_PCT_KEY)) || 100,
            luxPct:   parseInt(localStorage.getItem(PROD_LUX_PCT_KEY))  || 100,
            maximise: localStorage.getItem(PROD_MAX_KEY) === 'true',
        };

        const tpl      = await this.render('townhall-prod', { ikabotDetected, saved });
        const $section = $(tpl);
        $section.attr('id', 'ikaeasy_prod_wrap');

        const $anchor = $('#townHall, .contentBox01h').last();
        $anchor.after($section);

        if (ikabotDetected) {
            this._bindEvents($section);
            this._refreshSliderVisibility($section, saved.types);
        }
    }

    _refreshSliderVisibility($section, types) {
        $('#ikaeasy_prod_wood_row', $section).toggle(types === 'wood' || types === 'both');
        $('#ikaeasy_prod_lux_row',  $section).toggle(types === 'luxury' || types === 'both');
    }

    _bindEvents($section) {
        // Scope toggle.
        $section.on('change', 'input[name="ikaeasy_prod_scope"]', function () {
            localStorage.setItem(PROD_SCOPE_KEY, $(this).val());
        });

        // Resource type selector.
        $section.on('change', 'input[name="ikaeasy_prod_types"]', (e) => {
            const types = $(e.currentTarget).val();
            localStorage.setItem(PROD_TYPES_KEY, types);
            this._refreshSliderVisibility($section, types);
        });

        // Slider label sync + save.
        $section.on('input', '#ikaeasy_prod_wood_slider', function () {
            $('#ikaeasy_prod_wood_label', $section).text($(this).val() + '%');
            localStorage.setItem(PROD_WOOD_PCT_KEY, $(this).val());
        });
        $section.on('input', '#ikaeasy_prod_lux_slider', function () {
            $('#ikaeasy_prod_lux_label', $section).text($(this).val() + '%');
            localStorage.setItem(PROD_LUX_PCT_KEY, $(this).val());
        });

        // Maximise checkbox — only relevant when a slider is at 100%.
        $section.on('change', '#ikaeasy_prod_maximise', function () {
            localStorage.setItem(PROD_MAX_KEY, $(this).is(':checked'));
        });

        // Apply.
        $section.on('click', '#ikaeasy_prod_apply', (e) => {
            e.preventDefault();
            this._applyProduction($section);
        });
    }

    async _applyProduction($section) {
        const $status  = $('#ikaeasy_prod_status', $section);
        const scope    = $('input[name="ikaeasy_prod_scope"]:checked', $section).val();
        const types    = $('input[name="ikaeasy_prod_types"]:checked', $section).val();
        const woodPct  = parseInt($('#ikaeasy_prod_wood_slider', $section).val()) || 100;
        const luxPct   = parseInt($('#ikaeasy_prod_lux_slider',  $section).val()) || 100;
        const maximise = $('#ikaeasy_prod_maximise', $section).is(':checked');

        // Build city ID list.
        let cityIds = [];
        if (scope === 'all') {
            _.each(this._data.cities, (city, key) => {
                if (key.indexOf('city_') === 0) cityIds.push(parseInt(city.id));
            });
        } else {
            cityIds = [this._city.cityId];
        }

        const resourceTypes = [];
        if (types === 'wood' || types === 'both')   resourceTypes.push('resource');
        if (types === 'luxury' || types === 'both') resourceTypes.push('tradegood');

        $status.css('color', '').text('Applying…');
        $('#ikaeasy_prod_apply', $section).prop('disabled', true);

        try {
            const resp = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy`,
                {
                    method:      'POST',
                    credentials: 'include',
                    headers:     { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        ikaeasy_action: 'modify_production',
                        city_ids:       cityIds,
                        resource_types: resourceTypes,
                        wood_pct:       woodPct,
                        luxury_pct:     luxPct,
                        maximise:       maximise,
                    }),
                }
            );
            const data = await resp.json();
            if (data.ok) {
                const ok  = (data.results || []).filter(r => r.ok).length;
                const all = (data.results || []).length;
                $status.css('color', 'green').text(
                    `Done — ${ok} of ${all} city(s) updated.`
                );
            } else {
                $status.css('color', 'red').text(data.error || 'Failed.');
            }
        } catch (_) {
            $status.css('color', 'red').text('Could not reach ikabot.');
        } finally {
            $('#ikaeasy_prod_apply', $section).prop('disabled', false);
        }
    }
}

export default Page;
