class Military {
    constructor(city) {
        this.city = city;
        this.units = {};

        this.training = [];
    }

    load(data) {
        this.units    = data.units;
        this.training = data.training;
    }

    setCount(type, cnt) {
        if ((cnt === 0) && (this.units[type])) {
            delete this.units[type];
        }

        if (cnt === 0) {
            return;
        }

        this.units[type] = cnt;
        this.city.save();
    }

    setTraining(type, list) {
        this.training = _.filter(this.training, (u) => {
            return u.type !== type;
        });

        this.training = this.training.concat(list);
        this.city.save();
    }

    toSave() {
        return {
            units: this.units,
            training: this.training
        };
    }
}

export default Military;
