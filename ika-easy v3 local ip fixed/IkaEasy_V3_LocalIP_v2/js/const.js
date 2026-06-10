export const VERSION = '@@REV';

export const Research = Object.freeze({
    Seafaring: {
        CARPENTRY: 2150,
        DECK_WEAPONS: 1010,
        PIRACY: 1170,
        SHIP_MAINTENANCE: 1020,
        DRAFT: 1130,
        EXPANSION: 1030,
        FOREIGN_CULTURES: 1040,
        PITCH: 1050,
        MARKET: 2070,
        GREEK_FIRE: 1060,
        COUNTERWEIGHT: 1070,
        DIPLOMACY: 1080,
        SEA_MAPS: 1090,
        PADDLE_WHEEL_ENGINE: 1100,
        CAULKING: 1140,
        MORTAR_ATTACHMENT: 1110,
        MASSIVE_RAM: 1150,
        OFFSHORE_BASE: 1160,
        SEAFARING_FUTURE: 1999
    },

    Economy: {
        CONSERVATION: 2010,
        PULLEY: 2020,
        WEALTH: 2030,
        WINE_CULTURE: 2040,
        IMPROVED_RESOURCE_GATHERING: 2130,
        GEOMETRY: 2060,
        ARCHITECTURE: 1120,
        HOLIDAY: 2080,
        LEGISLATION: 2170,
        CULINARY_SPECIALITIES: 2050,
        HELPING_HANDS: 2090,
        SPIRIT_LEVEL: 2100,
        WINE_PRESS: 2140,
        DEPOT: 2160,
        BUREACRACY: 2110,
        UTOPIA: 2120,
        ECONOMIC_FUTURE: 2999
    },

    Science: {
        WELL_CONSTRUCTION: 3010,
        PAPER: 3020,
        ESPIONAGE: 3030,
        POLYTHEISM: 3040,
        INK: 3050,
        GOVERNMENT_FORMATION: 3150,
        INVENTION: 3140,
        CULTURAL_EXCHANGE: 3060,
        ANATOMY: 3070,
        OPTICS: 3080,
        EXPERIMENTS: 3081,
        MECHANICAL_PEN: 3090,
        BIRDS_FLIGHT: 3100,
        LETTER_CHUTE: 3110,
        STATE_RELIGION: 3160,
        PRESSURE_CHAMBER: 3120,
        ARCHIMEDEAN_PRINCIPLE: 3130,
        SCIENTIFIC_FUTURE: 3999
    },

    Military: {
        DRY_DOCKS: 4010,
        MAPS: 4020,
        PROFESSIONAL_ARMY: 4030,
        SEIGE: 4040,
        CODE_OF_HONOR: 4050,
        BALLISTICS: 4060,
        LAW_OF_THE_LEVEL: 4070,
        GOVERNOR: 4080,
        PYROTECHNICS: 4130,
        LOGISTICS: 4090,
        GUNPOWDER: 4100,
        ROBOTICS: 4110,
        CANNON_CASTING: 4120,
        MILITARISTIC_FUTURE: 4999
    }
});

export const Government = Object.freeze({
    ANARCHY:      'anarchie',
    IKACRACY:     'ikakratie',
    ARISTOCRACY:  'aristokratie',
    DICTATORSHIP: 'diktatur',
    DEMOCRACY:    'demokratie',
    NOMOCRACY:    'nomokratie',
    OLIGARCHY:    'oligarchie',
    TECHNOCRACY:  'technokratie',
    THEOCRACY:    'theokratie'
});

export const TradeGoodOrdinals = Object.freeze({
    WINE: 1,
    MARBLE: 2,
    GLASS: 3,
    SULFUR: 4,
    WOOD: 5
});

export const Resources = {
    WOOD: 'wood',
    WINE: 'wine',
    MARBLE: 'marble',
    GLASS: 'glass',
    SULFUR: 'sulfur',

    POPULATION: 'population',
    CITIZENS: 'citizens',

    SCIENTISTS: 'scientists',
    ACTION_POINTS: 'actionPoints',
    CULTURAL_GOODS: 'culturalGoods',
    TAVERN_WINE_LEVEL: 'tavernWineLevel',
    PRIESTS: 'priests',

    WINE_SPENDING: 'wineSpendings'
};

export const GamePlay = Object.freeze({
    TOWN_SPOTS: 17,
    BUILDING_SPOTS: 19,
    GOLD_PER_CITIZEN: 3,
    HAPPINESS_PER_CULTURAL_GOOD: 50,
    HAPPINESS_PER_WINE_SERVING_LEVEL: 60,
    BASE_RESOURCE_PROTECTION: 100,
    RESOURCES_PER_TRANSPORT: 500,
    RESOURCE_PROTECTION_WAREHOUSE: 480,
    RESOURCE_PROTECTION_WAREHOUSE_INACTIVE: 80
});

export const Military = Object.freeze({
    // Army
    HOPLITE: 'phalanx',
    STEAM_GIANT: 'steamgiant',
    SPEARMAN: 'spearman',
    SWORDSMAN: 'swordsman',
    SLINGER: 'slinger',
    ARCHER: 'archer',
    GUNNER: 'marksman',
    BATTERING_RAM: 'ram',
    CATAPULT: 'catapult',
    MORTAR: 'mortar',
    GYROCOPTER: 'gyrocopter',
    BALLOON_BOMBADIER: 'bombardier',
    COOK: 'cook',
    DOCTOR: 'medic',
    SPARTAN: 'spartan',
    ARMY: 'army',

    // Navy
    RAM_SHIP: 'ship_ram',
    FLAME_THROWER: 'ship_flamethrower',
    STEAM_RAM: 'ship_steamboat',
    BALLISTA_SHIP: 'ship_ballista',
    CATAPULT_SHIP: 'ship_catapult',
    MORTAR_SHIP: 'ship_mortar',
    SUBMARINE: 'ship_submarine',
    PADDLE_SPEED_SHIP: 'ship_paddlespeedship',
    BALLOON_CARRIER: 'ship_ballooncarrier',
    TENDER: 'ship_tender',
    ROCKET_SHIP: 'ship_rocketship',
    NAVY: 'navy'
});

export const CityType = Object.freeze({
    OWN: 'ownCity',
    DEPLOYMENT: 'deployedCities',
    OCCUPATION: 'occupiedCities',
    FOREIGN: 'foreign'
});

export const PlayerState = Object.freeze({
    INACTIVE: 'inactive',
    NORMAL: '',
    VACATION: 'vacation',
    NEW: 'noob'
});

export const CombatType = Object.freeze({
    BLOCKADE: 'blockade',
    PILLAGE: 'plunder'
});

export const UnitData = Object.freeze({
    spearman: {
        minimumBuildingLevelToBuild: 1,
        baseBuildTime: 60,
        isArmy: true,
        speed: 60,
        cargoSize: 3
    },
    slinger: {
        minimumBuildingLevelToBuild: 2,
        baseBuildTime: 90,
        isArmy: true,
        speed: 60,
        cargoSize: 3
    },
    ram: {
        minimumBuildingLevelToBuild: 3,
        baseBuildTime: 600,
        isArmy: true,
        speed: 40,
        cargoSize: 30
    },
    phalanx: {
        minimumBuildingLevelToBuild: 4,
        baseBuildTime: 300,
        isArmy: true,
        speed: 60,
        cargoSize: 5
    },
    cook: {
        minimumBuildingLevelToBuild: 5,
        baseBuildTime: 1200,
        isArmy: true,
        speed: 40,
        cargoSize: 20
    },
    swordsman: {
        minimumBuildingLevelToBuild: 6,
        baseBuildTime: 180,
        isArmy: true,
        speed: 60,
        cargoSize: 3
    },
    archer: {
        minimumBuildingLevelToBuild: 7,
        baseBuildTime: 240,
        isArmy: true,
        speed: 60,
        cargoSize: 5
    },
    catapult: {
        minimumBuildingLevelToBuild: 8,
        baseBuildTime: 1800,
        isArmy: true,
        speed: 40,
        cargoSize: 30
    },
    medic: {
        minimumBuildingLevelToBuild: 9,
        baseBuildTime: 1200,

        isArmy: true,
        speed: 60,
        cargoSize: 10
    },
    spartan: {
        minimumBuildingLevelToBuild: 99,
        baseBuildTime: 0,

        isArmy: true,
        speed: 60,
        cargoSize: 5
    },
    gyrocopter: {
        minimumBuildingLevelToBuild: 10,
        baseBuildTime: 900,
        isArmy: true,
        speed: 80,
        cargoSize: 15
    },
    bombardier: {
        minimumBuildingLevelToBuild: 11,
        baseBuildTime: 1800,
        isArmy: true,
        speed: 20,
        cargoSize: 30
    },
    steamgiant: {
        minimumBuildingLevelToBuild: 12,
        baseBuildTime: 900,
        isArmy: true,
        speed: 40,
        cargoSize: 15
    },
    marksman: {
        minimumBuildingLevelToBuild: 13,
        baseBuildTime: 600,
        isArmy: true,
        speed: 60,
        cargoSize: 5
    },
    mortar: {
        minimumBuildingLevelToBuild: 14,
        baseBuildTime: 2400,
        isArmy: true,
        speed: 40,
        cargoSize: 30
    },

    barbarian: {
        minimumBuildingLevelToBuild: 1,
        baseBuildTime: 1,
        isArmy: true,
        speed: 40,
        cargoSize: 5
    },

    ship_ram: {
        minimumBuildingLevelToBuild: 1,
        baseBuildTime: 2400,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_flamethrower: {
        minimumBuildingLevelToBuild: 4,
        baseBuildTime: 1800,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_submarine: {
        minimumBuildingLevelToBuild: 19,
        baseBuildTime: 3600,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_ballista: {
        minimumBuildingLevelToBuild: 3,
        baseBuildTime: 3000,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_catapult: {
        minimumBuildingLevelToBuild: 3,
        baseBuildTime: 3000,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_mortar: {
        minimumBuildingLevelToBuild: 17,
        baseBuildTime: 3000,
        isArmy: false,
        speed: 30,
        cargoSize: 0
    },
    ship_steamboat: {
        minimumBuildingLevelToBuild: 15,
        baseBuildTime: 2400,
        isArmy: false,
        speed: 40,
        cargoSize: 0
    },
    ship_rocketship: {
        minimumBuildingLevelToBuild: 11,
        baseBuildTime: 3600,
        isArmy: false,
        speed: 30,
        cargoSize: 0
    },
    ship_paddlespeedship: {
        minimumBuildingLevelToBuild: 13,
        baseBuildTime: 1800,
        isArmy: false,
        speed: 60,
        cargoSize: 0
    },
    ship_ballooncarrier: {
        minimumBuildingLevelToBuild: 7,
        baseBuildTime: 3900,
        isArmy: false,
        speed: 20,
        cargoSize: 0
    },
    ship_tender: {
        minimumBuildingLevelToBuild: 9,
        baseBuildTime: 2400,
        isArmy: false,
        speed: 30,
        cargoSize: 0
    }
});

export const UnitIds = Object.freeze({
    301: 'slinger',
    302: 'swordsman',
    303: 'phalanx',
    304: 'marksman',
    305: 'mortar',
    306: 'catapult',
    307: 'ram',
    308: 'steamgiant',
    309: 'bombardier',
    310: 'cook',
    311: 'medic',
    312: 'gyrocopter',
    313: 'archer',
    315: 'spearman',
    316: 'barbarian',
    319: 'spartan',

    210: 'ship_ram',
    211: 'ship_flamethrower',
    212: 'ship_submarine',
    213: 'ship_ballista',
    214: 'ship_catapult',
    215: 'ship_mortar',
    216: 'ship_steamboat',
    217: 'ship_rocketship',
    218: 'ship_paddlespeedship',
    219: 'ship_ballooncarrier',
    220: 'ship_tender'
});

export const Buildings = {
    TOWN_HALL: 'townHall',
    PALACE: 'palace',
    GOVERNORS_RESIDENCE: 'palaceColony',
    TAVERN: 'tavern',
    MUSEUM: 'museum',
    ACADEMY: 'academy',
    WORKSHOP: 'workshop',
    TEMPLE: 'temple',
    EMBASSY: 'embassy',
    WAREHOUSE: 'warehouse',
    DUMP: 'dump',
    TRADING_PORT: 'port',
    TRADING_POST: 'branchOffice',
    BLACK_MARKET: 'blackMarket',
    MARINE_CHART_ARCHIVE: 'marineChartArchive',
    WALL: 'wall',
    HIDEOUT: 'safehouse',
    BARRACKS: 'barracks',
    SHIPYARD: 'shipyard',
    PIRATE_FORTRESS: 'pirateFortress',
    FORESTER: 'forester',
    CARPENTER: 'carpentering',
    WINERY: 'winegrower',
    WINE_PRESS: 'vineyard',
    STONEMASON: 'stonemason',
    ARCHITECT: 'architect',
    GLASSBLOWER: 'glassblowing',
    OPTICIAN: 'optician',
    ALCHEMISTS_TOWER: 'alchemist',
    FIREWORK_TEST_AREA: 'fireworker'
};

export const BuildingsId = {
    'townHall': 0,
    'palace': 11,
    'palaceColony': 17,
    'tavern': 9,
    'museum': 10,
    'academy': 4,
    'workshop': 15,
    'temple': 28,
    'embassy': 12,
    'warehouse': 7,
    'dump': 29,
    'port': 3,
    'branchOffice': 13,
    'blackMarket': 31,
    'marineChartArchive': 32,
    'wall': 8,
    'safehouse': 16,
    'barracks': 6,
    'shipyard': 5,
    'pirateFortress': 30,
    'forester': 18,
    'carpentering': 23,
    'winegrower': 21,
    'vineyard': 26,
    'stonemason': 19,
    'architect': 24,
    'glassblowing': 20,
    'optician': 25,
    'alchemist': 22,
    'fireworker': 27
};

export const BuildingsMultiple = {
    [Buildings.WAREHOUSE] : true,
    [Buildings.TRADING_PORT] : true,
    [Buildings.SHIPYARD] : true
};

export const Time = {
    SECONDS_PER_HOUR: 3600,
    SECONDS_PER_MINUTE: 60,
    MILLIS_PER_DAY: 1000 * 60 * 60 * 24,
    MILLIS_PER_HOUR: 1000 * 60 * 60,
    MILLIS_PER_SECOND: 1000,
    MILLIS_PER_MINUTE: 60000,
    MINUTES_PER_DAY: 24 * 60,
    MINUTES_PER_HOUR: 60,
    HOURS_PER_DAY: 24,
    HOURS_PER_WEEK: 24 * 7,

    SAFE_TIME_DELTA: 1000,
    INITIAL_PAGE_LOAD_DELTA: 2000,

    TIMEUNITS: {
        second: 1,
        minute: 60,
        hour: 3600,
        day: 86400
    }
};

export const Movements = {
    Mission: {
        TRANSPORT: 'transport',
        DEPLOY_ARMY: 'deployarmy',
        DEPLOY_NAVY: 'deploynavy',
        PLUNDER: 'plunder',
        TRADE: 'trade'
    },

    Stage: {
        LOADING: 'loading',
        EN_ROUTE: 'en_route',
        RETURNING: 'returning'
    },

    MissionId: {
        TRANSPORT: 1,
        TRADE: 3,
        COLONIZE: 4,

        PLUNDER: 7
    },

    MissionState: {
        LOADING: 1,
        EN_ROUTE: 2
    }
};

export const PremiumFeatures = {
    DOUBLED_STORAGE_CAPACITY: '33',// "Увеличенная вместительность складов"
    DOUBLED_SAFE_CAPACITY: "17",// "Увеличенная защита от разграбления"
    PREMIUM_ACCOUNT: "15", // "Премиум аккю"
    DOUBLE_WOOD: "16",//"Увеличено производство стройматериалов"
};

export const WINE_USE = [0, 4, 8, 13, 18, 24, 30, 37, 44, 51, 60, 68, 78, 88, 99, 110, 122, 136, 150, 165, 180, 197, 216, 235, 255, 277, 300, 325, 351, 378, 408, 439, 472, 507, 544, 584, 626, 670, 717, 766, 818, 874, 933, 995, 1060, 1129, 1202, 1280, 1362];
export const PORT_LOADING_SPEED = [10, 30, 60, 93, 129, 169, 213, 261, 315, 373, 437, 508, 586, 672, 766, 869, 983, 1108, 1246, 1398, 1565, 1748, 1950, 2172, 2416, 2685, 2980, 3305, 3663, 4056, 4489, 4965, 5488, 6064, 6698, 7394, 8161, 9004, 9931, 10951, 12073, 13308, 14666, 16159, 17803, 19616, 21613, 23813, 26237];
export const MAX_SCIENTISTS =  [0, 8, 12, 16, 22, 28, 35, 43, 51, 60, 69, 79, 89, 100, 111, 122, 134, 146, 159, 172, 185, 198, 212, 227, 241, 256, 271, 287, 302, 318, 335, 351, 368];
