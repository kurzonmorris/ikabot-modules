'use strict';

class Notif {
    constructor() {
        this.checkPermission();
        this.advisers.init();
    }

    checkPermission() {
        if (Notification.permission !== 'granted') {
            Notification.requestPermission();
        }
    }


}

export default new Notif();