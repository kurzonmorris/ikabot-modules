import getLangs from './langs.js';
import DEF_OPTIONS from '../../options.js';
import {default as Notification} from './notification.js';

const ALARM_NAME_CHECKER = 'checker_start';
const STORE_KEY_CHECKER_STARTED = 'checker_started';

class Checker {
    constructor() {
        chrome.storage.onChanged.addListener((changes) => {
            if (!changes[STORE_KEY_CHECKER_STARTED]) {
                this.checkData.call(this);
            }
        });

        chrome.alarms.onAlarm.addListener((alarm) => {
            if (alarm.name === ALARM_NAME_CHECKER) {
                this.checkData.call(this);
            }
        });

        this.checkData();
    }

    setCheckerStarted(val) {
        let data = {};
        data[STORE_KEY_CHECKER_STARTED] = val;
        chrome.storage.local.set(data);
    }

    checkData() {
        this.stop();

        chrome.storage.local.get((result) => {
            if (result[STORE_KEY_CHECKER_STARTED]) {
                return;
            }

            this.setCheckerStarted(true);

            let timer = {};
            let saveData = {};

            for(let key in result) {
                if ((result[key]) && (key.indexOf('-ikaeasy') > -1)) {
                    let data = JSON.parse(result[key]);
                    if (!data.options) {
                        console.error('Options is undefined');
                        return;
                    }

                    let requireInteraction = !this.getOptions(data.options, 'notification_wait_no_interaction');
                    let needSave = [];

                    let lang = getLangs(data.server.lang);
                    if ((this.getOptions(data.options, 'notification_building_complete')) || (this.getOptions(data.options, 'notification_building_complete_prevent'))) {
                        let ns = this.checkBuildings(lang, data, requireInteraction, timer);
                        needSave.push(ns);
                    }

                    if (this.getOptions(data.options, 'notification_recruiting_complete')) {
                        let ns = this.checkUnits(lang, data, requireInteraction, timer);
                        needSave.push(ns);
                    }

                    let ns = this.checkMovements(lang, data, requireInteraction, timer);
                    needSave.push(ns);

                    if (_.some(needSave)) {
                        saveData[key] = JSON.stringify(data);
                    }
                }
            }

            if (_.size(saveData) > 0) {
                chrome.storage.local.set(saveData);
            }

            let soon = _.min(_.values(timer));

            if (typeof soon !== 'undefined') {
                chrome.alarms.create(ALARM_NAME_CHECKER, {
                    delayInMinutes: (soon + 2000) / 1000 / 60
                });
            }

            this.setCheckerStarted(false);
        });
    }

    checkBuildings(lang, data, requireInteraction, timer) {
        let now = Date.now();
        let cities = data.cities;
        let needSave = false;

        let nextTime = (time) => {
            if (typeof timer.buildings === 'undefined') {
                timer.buildings = time - now;
            } else {
                timer.buildings = Math.min(timer.buildings, time - now);
            }
        };

        for (let cityId in cities) {
            let city = cities[cityId].city;
            if ((city.isOwn) && (city.buildings)) {
                for (let name in city.buildings) {

                    city.buildings[name].forEach((v) => {
                        if (!v.completed) {
                            return;
                        }

                        if (this.getOptions(data.options, 'notification_building_complete')) {
                            let completed = v.completed * 1000;

                            if (completed < now) {
                                let notificationId = this.createNotificationId(data.server, {
                                    action: 'building',
                                    cityId: city.id,
                                    building: v.building,
                                    completed: v.completed,
                                    prevent: false
                                });

                                let title = lang.getLocalizedString('notif.building_upgrade_complete');
                                let message = lang.getLocalizedString('notif.building_upgrade_complete_text', {
                                    CITY: city.name,
                                    BUILDING: v.name,
                                    LEVEL: v.level + 1
                                });

                                Notification.send(notificationId, title, message, requireInteraction);

                                // Скрываем уведомление о скором завершении строительства
                                let notificationIdPrevent = _.clone(notificationId);
                                notificationIdPrevent.prevent = true;

                                Notification.hide(notificationIdPrevent);

                                needSave = true;
                                v.completed = null;
                                v.level += 1;

                                return;
                            } else {
                                nextTime(completed);
                            }
                        }

                        if (this.getOptions(data.options, 'notification_building_complete_prevent')) {
                            let completed = (v.completed - 180) * 1000; // за 3 минуты

                            if (completed < now) {
                                let notificationId = this.createNotificationId(data.server, {
                                    action: 'building',
                                    cityId: city.id,
                                    building: v.building,
                                    completed: v.completed,
                                    prevent: true
                                });

                                let title = lang.getLocalizedString('notif.building_upgrade_complete_prevent');
                                let message = lang.getLocalizedString('notif.building_upgrade_complete_prevent_text', {
                                    CITY: city.name,
                                    BUILDING: v.name,
                                    LEVEL: v.level + 1
                                });

                                Notification.send(notificationId, title, message, requireInteraction);
                            } else {
                                nextTime(completed);
                            }
                        }
                    });
                }
            }
        }

        return needSave;
    }

    checkMovements(lang, data, requireInteraction, timer) {
        let now = Date.now();
        let nextTime = (time) => {
            if (typeof timer.movement === 'undefined') {
                timer.movement = time - now;
            } else {
                timer.movement = Math.min(timer.movement, time - now);
            }
        };

        let needSave = false;
        let toRemove = {};
        _.each(data.movements, (movement) => {
            let stageIndex = _.findIndex(movement.stages, (v) => {
                if (movement.startTime + v.time > now) {
                    return true;
                }
            });

            if (stageIndex === 0) {
                nextTime(movement.startTime + movement.stages[stageIndex].time);

                // Ниодна стадия еще не завершена
                return;
            }

            if (stageIndex === -1) {
                // Финальный stage выполнен
                stageIndex = movement.stages.length - 1;
            } else {
                // Получаем индекс именно выполненной стадии, а не текущей
                nextTime(movement.startTime + movement.stages[stageIndex].time);
                stageIndex -= 1;
            }

            let stage = movement.stages[stageIndex];
            if (!this.getOptions(data.options, `notification_transport_${ stage.stage }`)) {
                return;
            }

            let notificationId = this.createNotificationId(data.server, {
                action: 'movement',
                stage: stage.stage,
                isFinish: stageIndex === (movement.stages.length - 1),
                id: movement.id
            });

            let cityName = '';
            if (['loading', 'returning'].indexOf(stage.stage) > -1) {
                cityName = movement.originCityName;
                notificationId.cityId = movement.originCityId;
            } else if (movement.mission === 'trade') {
                if (notificationId.isFinish) {
                    cityName = movement.originCityName;
                    notificationId.cityId = movement.originCityId;
                } else {
                    cityName = movement.targetCityName;
                    notificationId.cityId = movement.targetCityId;
                }
            } else {
                cityName = movement.targetCityName;
                notificationId.cityId = movement.targetCityId;
            }

            let title = lang.getLocalizedString('notif.movements_mission_update');
            let message = lang.getLocalizedString(`notif.movements_${stage.stage}_text`, {CITY: cityName, TYPE: lang.getLocalizedString(`movement.${movement.mission}`)});

            Notification.send(notificationId, title, message, requireInteraction);

            if ((notificationId.isFinish) && (['transport', 'trade'].indexOf(movement.mission) > -1)) {
                // Прибыл транспорт с ресурсами... пополняем в нужном городе
                needSave = true;
                let city = data.cities[movement.targetCityId];
                if (stage.stage === 'returning') {
                    // Вернулся в город отправления
                    city = data.cities[movement.originCityId];
                }

                if (city) {
                    _.each(movement.resources, (v, k) => {
                        city.city.resources[k] += v;
                    });
                }
            }

            if (notificationId.isFinish) {
                toRemove[movement.id] = true;
                needSave = true;
            }
        });

        data.movements = _.filter(data.movements, (m) => {
            return !toRemove[m.id];
        });

        return needSave;
    }

    checkUnits(lang, data, requireInteraction, timer) {
        let now = Date.now();
        let cities = data.cities;

        let nextTime = (time) => {
            if (typeof timer.units === 'undefined') {
                timer.units = time - now;
            } else {
                timer.units = Math.min(timer.units, time - now);
            }
        };

        let needSave = false;
        _.each(data.cities, (city) => {
            if (!city.city.isOwn)  {
                return;
            }

            if ((city.military.training) && (city.military.training.length)) {
                city.military.training = _.filter(city.military.training, (u) => {
                    if (u.time > now) {
                        nextTime(u.time);
                        return true;
                    }

                    let building = (u.type === 'units') ? 'barracks' : 'shipyard';
                    let position = -1;
                    if ((city.city.buildings) && (city.city.buildings[building]) && (city.city.buildings[building][0])) {
                        position = city.city.buildings[building][0].position;
                    }

                    let notificationId = this.createNotificationId(data.server, {
                        action: 'army',
                        cityId: city.city.id,
                        building: building,
                        position: position,
                        now: now
                    });

                    let title = lang.getLocalizedString('notif.recruiting_complete');
                    let message = lang.getLocalizedString('notif.recruiting_complete_text', {CITY: city.city.name, UNIT_TYPE: lang.getLocalizedString(`unittype.${u.type}`) });

                    Notification.send(notificationId, title, message, requireInteraction);

                    needSave = true;

                    // Добавляем юнитов
                    _.each(u.units, (cnt, type) => {
                        if (typeof city.military.units[type] === 'number') {
                            city.military.units[type] += cnt;
                        } else {
                            city.military.units[type] = cnt;
                        }
                    });

                    return false;
                });
            }
        });

        return needSave;
    }

    getOptions(list, key) {
        if (typeof list[key] !== 'undefined') {
            return list[key];
        }

        return DEF_OPTIONS[key];
    }

    createNotificationId(server, data) {
        let pre = {
            domain: server.domain,
            world: server.world,
            avatarId: server.avatarId,
            lang: server.lang
        };

        return _.merge(pre, data || {});
    }

    stop() {
        chrome.alarms.clear(ALARM_NAME_CHECKER);
    }
}

export default Checker;
