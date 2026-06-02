'use strict';

(function() {
    window.addEventListener('message', function (event) {
        if ((event.data.type) && ((event.data.type === 'FROM_IKAEASY_V3'))) {
            if (event.data.cmd === 'code_eval') {
                eval(event.data.code);
            }
        }
    });

    const CONSOLE_ENABLED = true;

    if (CONSOLE_ENABLED) {
        var i = document.createElement('iframe');
        i.style.display = 'none';
        document.body.appendChild(i);
        window.console = i.contentWindow.console;
    }

    class Front {
        constructor() {
            this._last = '';
            this._lastRequest = 0;
            this._viewData = {};

            this._updateResources();

            // Инициализируем таймер, который будет искать изменения
            setInterval(this.loop.bind(this), 50);

            let self = this;
            if ((window.ajax) && (window.ajax.Responder) && (window.ajax.Responder.parseResponse)) {
                window.ajax.Responder.parseResponse = function (f) {
                    return function(resp) {

                        resp = JSON.parse(resp);
                        resp.forEach((r) => {
                            if (r[0] === 'changeView') {
                                let viewName = r[1][0];
                                self._viewData[viewName] = null;

                                if (typeof r[1][2] === 'object') {
                                    let rr = r[1][2];
                                    if (rr.viewScriptParams) {
                                        if (rr.viewScriptParams.localization) {
                                            delete rr.viewScriptParams.localization;
                                        }

                                        self._viewData[viewName] = rr.viewScriptParams;
                                    }
                                }
                                return false;
                            }
                        });

                        window.postMessage({ type: 'FROM_IKAEASY_V3', cmd: 'form', form: resp }, '*');
                        return f.apply(this, arguments);
                    };
                }(window.ajax.Responder.parseResponse);
            }
            this.httpListener();
        }

        send(data = null) {
            let tpl = this.getTemplateId();
            window.postMessage({ type: 'FROM_IKAEASY_V3', cmd: 'update', bg: this._getBgId(), tpl: tpl, data: data, viewData: this._viewData[tpl] }, '*');
        }

        loop() {
            let tpl = this.getTemplateId();

            if (!this._lastRequest) {
                this._lastRequest = ikariam.model.requestTime;
            }

            if (!tpl) {
                this._viewData = {};
            }

            if (((tpl !== this._last)) || (ikariam.model.requestTime !== this._lastRequest)) {
                this._updateResources();
                this._lastRequest = ikariam.model.requestTime;
                this._last = tpl;
            }
        }

        _updateResources() {
            if (typeof LocalizationStrings !== 'undefined') {
                LocalizationStrings.glass = LocalizationStrings.crystal;
            } else {
                LocalizationStrings.glass = 'Glass'
            }

            let model = ikariam.model;
            let trasferVars = {
                isOwnCity: model.isOwnCity,
                allyId: parseInt(model.avatarAllyId),
                avatarId: parseInt(model.avatarId),
                actionRequest: model.actionRequest,
                resources: model.currentResources,
                wineSpendings: model.wineSpendings,
                cities: model.relatedCityData,
                localizationStrings: LocalizationStrings,
                ships: model.freeTransporters,
                maxResources: model.maxResources,
                serverTimeOffset: (Date.now() - model.initialServerTime),
                initialBrowserTime: model.initialBrowserTime,
                serverName: model.serverName,
                maxActionPoints: model.maxActionPoints,
                advisorData: model.advisorData,
                gold: {
                    income: model.income || 0,
                    badTaxAccountant: model.badTaxAccountant || 0,
                    scientistsUpkeep: model.scientistsUpkeep || 0,
                    upkeep: model.upkeep || 0
                },

                producedTradegood: model.producedTradegood,
                tradegoodProduction: model.tradegoodProduction,
                resourceProduction: model.resourceProduction,
            };

            if (ikariam.templateView) {
                trasferVars.templateView = ikariam.templateView.script;
            } else {
                trasferVars.templateView = null;
            }

            trasferVars.cities.selectedCityId = parseInt(trasferVars.cities.selectedCity.replace('city_', ''));

            if (this._getBgId() === 'city') {
                trasferVars.city = ikariam.backgroundView.screen.data;
                trasferVars.island = { islandId: ikariam.backgroundView.screen.data.islandId };
            } else if (this._getBgId() === 'island') {
                let data = ikariam.backgroundView.screen.data;
                trasferVars.island = {
                    islandId:   data.id,
                    tradegood:  data.tradegoodLevel,
                    wonderType: data.wonder,
                    wonder:     data.wonderLevel,
                    wood:       data.resourceLevel,
                    cities:     data.cities
                };
            }

            this.send(trasferVars);
        }

        getTemplateId (){
            if ((ikariam.templateView) && (ikariam.templateView.id)) {
                return ikariam.templateView.id;
            }

            return null;
        }

        _getBgId(){
            return ikariam.backgroundView.id || document.getElementsByTagName('body')[0].id;
        }

        
        httpListener(){
            $(document).ajaxSuccess(function(event, request, options) {
                window.postMessage({
                    type: 'FROM_IKAEASY_V3',
                    cmd: 'ajax',
                    request: {
                        type: options.type,
                        data: request.responseText,
                        url: options.url
                    }
                });
            });
        }
        
    }

    const _front = new Front();
})();