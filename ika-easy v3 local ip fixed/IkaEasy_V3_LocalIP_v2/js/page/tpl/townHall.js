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
        await this.addCmReport();
    }

    async addProductionSection() {
        if ($('#ikaeasy_prod_wrap').length) {
            return;
        }

        // Only show when playing through the ikabot proxy, and only enable the
        // controls if the Resource Production Manager module is installed.
        let ikabotDetected  = false;
        let moduleAvailable = false;
        try {
            const r = await fetch(
                `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=prod_status`,
                { credentials: 'include', signal: AbortSignal.timeout(2000) }
            );
            if (r.ok) {
                ikabotDetected = true;
                const data = await r.json();
                moduleAvailable = !!data.available;
            }
        } catch (_) {}

        let savedTypes = localStorage.getItem(PROD_TYPES_KEY) || 'wood_then_luxury';
        // Migrate the old 'both' value to the new ordered default.
        if (savedTypes === 'both') savedTypes = 'wood_then_luxury';

        const saved = {
            scope:    localStorage.getItem(PROD_SCOPE_KEY)    || 'city',
            types:    savedTypes,
            woodPct:  parseInt(localStorage.getItem(PROD_WOOD_PCT_KEY)) || 100,
            luxPct:   parseInt(localStorage.getItem(PROD_LUX_PCT_KEY))  || 100,
            maximise: localStorage.getItem(PROD_MAX_KEY) === 'true',
        };

        const tpl      = await this.render('townhall-prod', { ikabotDetected, moduleAvailable, saved });
        const $section = $(tpl);
        $section.attr('id', 'ikaeasy_prod_wrap');

        const $anchor = $('#townHall, .contentBox01h').last();
        $anchor.after($section);

        if (ikabotDetected && moduleAvailable) {
            this._bindEvents($section);
            this._refreshSliderVisibility($section, saved.types);
        }
    }

    _refreshSliderVisibility($section, types) {
        const wantsWood = (types === 'wood' || types === 'wood_then_luxury' || types === 'luxury_then_wood');
        const wantsLux  = (types === 'luxury' || types === 'wood_then_luxury' || types === 'luxury_then_wood');
        $('#ikaeasy_prod_wood_row', $section).toggle(wantsWood);
        $('#ikaeasy_prod_lux_row',  $section).toggle(wantsLux);
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
        const mode     = $('input[name="ikaeasy_prod_types"]:checked', $section).val();
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
                        mode:           mode,
                        wood_pct:       woodPct,
                        luxury_pct:     luxPct,
                        overcharge:     maximise,
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

    async addCmReport() {
        if ($('#ikaeasy_cm_report').length) return;

        let ikabotDetected = false;
        let rows = [];
        try {
            const cityId = this._city && this._city.cityId;
            const url = `${location.origin}/index.php?ikabot=1&action=ikaeasy&ikaeasy=construction`
                + (cityId ? `&city_id=${cityId}` : '');
            const r = await fetch(url, { credentials: 'include', signal: AbortSignal.timeout(3000) });
            if (r.ok) {
                const data = await r.json();
                ikabotDetected = true;
                const cityKey = cityId ? String(cityId) : Object.keys(data.cities || {})[0];
                rows = (data.cities && data.cities[cityKey]) || [];
            }
        } catch (_) {}

        // Sort by slot_position (queue order).
        rows.sort((a, b) => (a.slot_position || 0) - (b.slot_position || 0));

        // Build cumulative totals and group by building name.
        const groups = [];
        let cumWood = 0, cumWine = 0, cumMarble = 0, cumCrystal = 0, cumSulphur = 0;
        let lastBuilding = null;
        let currentGroup = null;

        // Current city resources and production rates for time estimates.
        const res  = (this._city && this._city.resources)  || {};
        const prod = (this._city && this._city.production)  || {};
        // IkaEasy resource keys: wood=5, wine=1, marble=2, glass=3, sulfur=4
        const stock = {
            wood:    res[5]  || res['wood']    || 0,
            wine:    res[1]  || res['wine']    || 0,
            marble:  res[2]  || res['marble']  || 0,
            crystal: res[3]  || res['glass']   || 0,
            sulphur: res[4]  || res['sulfur']  || 0,
        };
        const rate = {
            wood:    prod[5]  || prod['wood']   || 0,
            wine:    prod[1]  || prod['wine']   || 0,
            marble:  prod[2]  || prod['marble'] || 0,
            crystal: prod[3]  || prod['glass']  || 0,
            sulphur: prod[4]  || prod['sulfur'] || 0,
        };

        for (const row of rows) {
            cumWood    += row.wood    || 0;
            cumWine    += row.wine    || 0;
            cumMarble  += row.marble  || 0;
            cumCrystal += row.crystal || 0;
            cumSulphur += row.sulphur || 0;

            // Time-to-start: max hours needed across all resources for this cumulative total.
            let hoursNeeded = 0;
            for (const [res, cum] of [
                ['wood', cumWood], ['wine', cumWine], ['marble', cumMarble],
                ['crystal', cumCrystal], ['sulphur', cumSulphur],
            ]) {
                const deficit = cum - stock[res];
                if (deficit > 0 && rate[res] > 0) {
                    hoursNeeded = Math.max(hoursNeeded, deficit / rate[res]);
                }
            }

            let canStartLabel;
            if (hoursNeeded <= 0) {
                canStartLabel = 'Now';
            } else if (hoursNeeded < 1) {
                canStartLabel = `~${Math.ceil(hoursNeeded * 60)} min`;
            } else if (hoursNeeded < 48) {
                canStartLabel = `~${Math.ceil(hoursNeeded)} h`;
            } else {
                canStartLabel = `~${Math.ceil(hoursNeeded / 24)} d`;
            }

            const buildingName = row.building || 'Unknown';
            if (buildingName !== lastBuilding) {
                currentGroup = { buildingName, buildingKey: buildingName.toLowerCase().replace(/\s+/g, '_'), levels: [] };
                groups.push(currentGroup);
                lastBuilding = buildingName;
            }
            currentGroup.levels.push({
                target_level: row.target_level,
                cumWood, cumWine, cumMarble, cumCrystal, cumSulphur,
                canStartNow: hoursNeeded <= 0,
                canStartLabel,
            });
        }

        const tpl = await this.render('cm-report', { ikabotDetected, rows, groups });
        const $section = $(tpl);
        $section.attr('id', 'ikaeasy_cm_report');

        const $anchor = $('#ikaeasy_prod_wrap, #townHall, .contentBox01h').last();
        $anchor.after($section);
    }
}

export default Page;
