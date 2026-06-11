import DEF_OPTIONS from '../../options.js';

class Options {
    constructor() {
        this.list = DEF_OPTIONS;
    }

    get(name) {
        if (!Front.ikaeasyData.options) {
            Front.ikaeasyData.options = {};
            Front.ikaeasyData.save();
        }

        if (typeof Front.ikaeasyData.options[name] !== 'undefined') {
            return Front.ikaeasyData.options[name];
        }

        if (typeof this.list[name] === 'undefined') {
            return true;
        }

        return this.list[name];
    }

    set(name, value) {
        Front.ikaeasyData.options[name] = value;
        Front.ikaeasyData.save();
        SANDBOX.send('set_params', { ikaeasyDataOptions: Front.ikaeasyData.options });
    }

    getList() {
        return {
            'IkaEasy': [
                'island_ap',
                'island_ships_owner',
                'island_details',
                'world_search_island',
                'city_details',
                'city_building_tooltip',
                'dummy_resource_prod',
                'dummy_transporter',
                'units_max',
                'diplomacy_links',
                'diplomacy_tab_members',
                'military_movements',
                'transport_buttons',
                'auto_accept_daily_bonus',
                'prevent_accidental_colony_destruction',
                'empire',
                'notes',
                'city_hotkeys',
                'quick_menu',
            ],

            'option._ads': [
                'hide_premium',
                'hide_ads',
                'hide_happy_hour',
                'hide_friends_bar'
            ],

            'option._notification': [
                'notification_wait_no_interaction',
                'notification_building_complete',
                'notification_building_complete_prevent',
                'notification_recruiting_complete',
                'notification_transport_loading',
                'notification_transport_en_route',
                'notification_transport_returning',
                'notification_advisor'
            ]
        };
    }

    hasHint(key) {
        let k = `option.${key}_hint`;
        return !!LANGUAGE[k];
    }

}

export default new Options();
