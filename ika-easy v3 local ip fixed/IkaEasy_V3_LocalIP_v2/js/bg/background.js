import '../libs/lodash.js';
import Notification from './notification.js';
import Checker from './checker.js';

new Checker();

chrome.runtime.onMessage.addListener((message, sender, callback) => {
    switch (message.cmd) {
        case 'get-version':
            let details = chrome.runtime.getManifest() || {};
            callback(details.version || null);
            break;

        case 'ajax':
            ajax(message, callback);
            break;

        case 'ajax_html':
            ajaxHTML(message, callback);
            break;

        case 'notification':
            showNotification(message);
            break;
    }

    return true;
});

function showNotification(message) {
    Notification.send(message.id, message.title, message.body, message.requireInteraction);
}

function toQueryString(params = {}, prefix) {
    const query = Object.keys(params).map((k) => {
        let key = k;
        let value = params[key];

        if (!value && (value === null || value === undefined || isNaN(value))) {
            value = '';
        }

        switch (params.constructor) {
            case Array:
                key = `${prefix}[${key}]`;
                break;
            case Object:
                key = (prefix ? `${prefix}[${key}]` : key);
                break;
        }

        if (typeof value === 'object') {
            return toQueryString(value, key); // for nested objects
        }

        return `${key}=${encodeURIComponent(value)}`;
    });

    return query.join('&');
}

function ajax(message, callback) {
    let url = `https://ikalogs.ru/${message.url}`;
    let options = {credentials: 'include'};

    if ((message.method) && (message.method.toLowerCase() === 'post')) {
        options.method = 'POST';

        if (message.body) {
            options.body = toQueryString(message.body);

            options.headers = {
                'Content-type': 'application/x-www-form-urlencoded; charset=UTF-8'
            };
        }
    }

    if (message.hasOwnProperty('headers')) {
        options.headers = { ...options.headers, ...message.headers };
    }

    fetch(url, options)
        .then(response => response.json())
        .then((json) => {
            //console.log('ajax < ',json);
            callback(json);
        })
        .catch(error => {
            console.error('ajax < ', error);
            callback(null);
        });
}


function ajaxHTML(message, callback) {
    const url = message.url;
    let options = {credentials: 'include'};

    if ((message.method) && (message.method.toLowerCase() === 'post')) {
        options.method = 'POST';

        if (message.body) {
            options.body = toQueryString(message.body);

            options.headers = {
                'Content-type': 'application/x-www-form-urlencoded; charset=UTF-8'
            };
        }
    }

    //console.log('ajax > ', url, options);
    fetch(url, options)
        .then(response => response.text())
        .then((html) => {
            //console.log('ajax < ',json);
            callback(html);
        })
        .catch(error => {
            console.error('ajax < ', error);
            callback(null);
        });
}
