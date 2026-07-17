const STORE_KEY_SENDED_NOTIFICATIONS = 'sended_notifications';

class IkaeasyNotification {
    constructor() {
        chrome.notifications.onClicked.addListener(this.onClicked.bind(this));
    }

    onClicked(notificationId) {
        let data = null;
        try {
            data = JSON.parse(notificationId);
        } catch (e) {
            console.error(e);
            return;
        }

        // Ищем среди открытых вкладок, вкладку с игрой... может быть уже такая есть, тогда просто в ней меняем город
        let url = `https://${data.world}-${data.domain}.ikariam.gameforge.com/index.php`;
        let tabId = null;
        chrome.tabs.query({}, (tabs) => {
            _.each(tabs, (tab) => {
                if ((tab.url.indexOf(url) === 0) && ((!tabId) || (tab.active))) {
                    tabId = tab.id;
                }
            });

            let openUrl = null;

            switch(data.action) {
                case 'building':
                    openUrl = `${url}?view=city&cityId=${data.cityId}`;
                    break;

                case 'movement':
                    openUrl = `${url}?view=${(data.isFinish) ? 'tradeAdvisor' : 'militaryAdvisor'}&backgroundView=city`;
                    if (data.cityId) {
                        openUrl += `&cityId=${data.cityId}`;
                    }
                    break;

                case 'army':
                    openUrl = `${url}?backgroundView=city&cityId=${data.cityId}`;
                    if (data.position > -1) {
                        openUrl += `&view=${ data.building }&position=${ data.position }`;
                    }
                    break;

                case 'advisor':
                    switch (data.advisor) {
                        case 'cities': openUrl = `${url}?view=tradeAdvisor`; break;
                        case 'military': openUrl = `${url}?view=militaryAdvisor`; break;
                        case 'research': openUrl = `${url}?view=researchAdvisor`; break;
                        case 'diplomacy': openUrl = `${url}?view=diplomacyAdvisor`; break;
                        default: openUrl = `${url}`; break;
                    }
                    break;
            }


            if (openUrl) {
                if (tabId) {
                    chrome.tabs.update(tabId, {url: openUrl, active: true});
                } else {
                    chrome.tabs.create({url: openUrl});
                }
            }

            chrome.notifications.clear(notificationId);
        });
    }

    send(notificationId, title, message, requireInteraction) {
        if (typeof notificationId !== 'string') {
            notificationId = JSON.stringify(notificationId);
        }

        chrome.storage.local.get([STORE_KEY_SENDED_NOTIFICATIONS], (data) => {
            if (data[STORE_KEY_SENDED_NOTIFICATIONS] && data[STORE_KEY_SENDED_NOTIFICATIONS][notificationId]) {
                return;
            }

            if (!data[STORE_KEY_SENDED_NOTIFICATIONS]) {
                data[STORE_KEY_SENDED_NOTIFICATIONS] = {};
            }

            data[STORE_KEY_SENDED_NOTIFICATIONS][notificationId] = true;

            chrome.storage.local.set(data);

            chrome.notifications.create(notificationId, {
                type: 'basic',
                title: title,
                message: message,
                iconUrl: chrome.runtime.getURL("/icon/128.png"),
                requireInteraction: requireInteraction
            });
        });
    }

    hide(notificationId) {
        if (typeof notificationId !== 'string') {
            notificationId = JSON.stringify(notificationId);
        }

        chrome.notifications.clear(notificationId);
    }
}

export default new IkaeasyNotification();
