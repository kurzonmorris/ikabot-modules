import {Movements} from '../const.js';

class Movement {
    constructor(data) {
        this.resources = {};
        this.stages = [];
        this.startTime = null;

        if (data) {
            this.id = data.id;
            this.startTime = data.startTime || null;
            this.type = data.type;
            this.mission = data.mission;

            this.originCityId = data.originCityId;
            this.originCityName = data.originCityName;
            this.targetCityId = data.targetCityId;
            this.targetCityName = data.targetCityName;

            this.transports = data.transports;
            this.units = data.units || null;
            this.resources = data.resources || null;

            this.stages = data.stages || [];
        }

        if (this.startTime === null) {
            this.startTime = _.now();
            _.each(Front.ikaeasyData.movements, (m) => {
                if (m.originCityId === this.originCityId) {
                    const stage = m.getStage();

                    if ((stage) && (stage.stage === Movements.Stage.LOADING) && (this.startTime < stage.finishTime)) {
                        this.startTime = stage.finishTime;
                    }
                }
            });
        }
    }

    getId() {
        return this.id;
    }
    getMission() {
        return this.mission;
    }
    addStage(stage, time) {
        let prevTimes = 0;
        _.each(this.stages, (v) => {
            prevTimes += v.time;
        });
        this.stages.push({stage: stage, time: parseInt(time + prevTimes), start: prevTimes});
    }
    getStage() {
        let now = _.now();
        let stage = this.stages.find((v) => {
            if (this.startTime + v.time > now) {
                return true;
            }
        });

        if (stage) {
            return {
                stage: stage.stage,
                time: stage.time,
                start: stage.start,
                startTime: stage.start + this.startTime,
                finishTime: stage.time + this.startTime
            };
        }

        return null;
    }
    getOriginCity() {
        return this.originCityId && Front.ikaeasyData.getCity(this.originCityId);
    }
    getTargetCity() {
        return this.targetCityId && Front.ikaeasyData.getCity(this.targetCityId);
    }
    getUnits() {
        return this.units || null;
    }
    getResource(resourceName) {
        return this.resources[resourceName];
    }
    isHostile() {
        return this.type.indexOf('hostile') >= 0;
    }
    isOwn() {
        return this.type.indexOf('own') > -1;
    }

    toSave() {
        return {
            id: this.id,
            startTime: this.startTime,
            type: this.type,
            mission: this.mission,

            originCityId: this.originCityId,
            originCityName: this.originCityName,

            targetCityId: this.targetCityId,
            targetCityName: this.targetCityName,

            transports: this.transports,
            units: this.units,
            resources: this.resources,

            stages: this.stages
        };
    }
}

export default Movement;
