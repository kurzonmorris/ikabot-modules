class PremiumModule {
    constructor() {
        this._data = {};
    }

    updateData(data) {
        if (data && data.time && data.data) {
            this._time = data.time;
            this._data = data.data;

            if (this._time + 86400000 < _.now()) {
                // Данные не обновлялись более суток, обновим на всякий случай
                setTimeout(() => {
                    this.forceUpdate();
                }, _.random(15, 60) * 1000);
            }
        } else {
            // Данных об исследованиях еще нет, получаем их
            this.forceUpdate();
        }
    }

    update(data) {
        this._data = {};
        // e.g: "[{"type":"33","subType":0,"name":"Увеличенная вместительность складов","desc":"Вместительность Ваших складов удваивается.<br />После того, как паровой погрузчик завершит действие, Вы потеряете все ресурсы сверх обычной Вашей вместимости.","cssClass":"itemIcon storageCapacityBonus","cityId":"0","activeUntil":"1585210582","extendPossible":true,"extensionActive":false,"isCancelable":false,"isNegative":false,"extendLabel":"Автоматически продлить на <b>30 д. </b> для:","extendItemCssClass":"itemIcon storageCapacityBonus days thirtyDays","extendItemAmbrosiaCosts":108},{"type":"15","subType":0,"name":"Премиум аккаунт","desc":"Усовершенствованные обзоры, строительные квесты и улучшенные сообщения активны.","cssClass":"itemIcon premiumAccount","cityId":"0","activeUntil":"1586246684","extendPossible":true,"extensionActive":false,"isCancelable":false,"isNegative":false,"extendLabel":"Автоматически продлить на <b>30 д. </b> для:","extendItemCssClass":"itemIcon premiumAccount days thirtyDays","extendItemAmbrosiaCosts":55},{"type":"16","subType":0,"name":"Увеличено производство стройматериалов","desc":"На 20% больше стройматериалов будет добываться на всех островах.","cssClass":"itemIcon resourceBonus","cityId":"0","activeUntil":"1586274052","extendPossible":true,"extensionActive":false,"isCancelable":false,"isNegative":false,"extendLabel":"Автоматически продлить на <b>30 д. </b> для:","extendItemCssClass":"itemIcon resourceBonus days thirtyDays","extendItemAmbrosiaCosts":36},{"type":"17","subType":0,"name":"Увеличенная защита от разграбления","desc":"Больше пространства на складах защищено от разграбления.","cssClass":"itemIcon safeCapacityBonus","cityId":"0","activeUntil":"1590307213","extendPossible":true,"extensionActive":false,"isCancelable":false,"isNegative":false,"extendLabel":"Автоматически продлить на <b>30 д. </b> для:","extendItemCssClass":"itemIcon safeCapacityBonus days thirtyDays","extendItemAmbrosiaCosts":72}]"
        for(const item of data){
            this._data[item.type] = item;
        }

        this._time = _.now();
        Front.ikaeasyData.save();
    }

    toJSON() {
        return {
            time: this._time,
            data: this._data
        };
    }

    forceUpdate() {
        $.get(`/index.php?view=inventory&ajax=1`, (data) => {
            data = data[1][1][2].viewScriptParams.activeModifiers;
            this.update(data);
        }, 'json');
    }
}

export default new PremiumModule();
