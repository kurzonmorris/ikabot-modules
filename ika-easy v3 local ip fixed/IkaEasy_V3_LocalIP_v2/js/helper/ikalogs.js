import IkalogsRu from './ikalogsRu.js';
import { createDynamic, getItem, setItem, getInt, getServerDomain, getServerWorld } from '../utils.js';
import Render from './templater.js';

const CNT_ROUND_FOR_PART = 20;

class Ikalogs {
    constructor(avatarId) {
        this._user_id = avatarId;
        this._all_rounds = 0;
        this.make_box();
    }

    async render(tpl, data = {}) {
        return await Render(tpl, data)
    }

    async make_box() {
        if ($('#ikalogs_saver').length > 0) {
            $('#ikalogs_saver').parent().parent().parent().parent().remove();
        }

        const tpl = await this.render('ikalogs-dynamic', {});
        this.$el = await createDynamic(LANGUAGE.getLocalizedString('ikalogs_save_log'), tpl);
        $('#backTo').after(this.$el);

        let url = $('#troopsReport .contentBox01h p.link a').eq(0).attr('href');
        this._rep_id = url.match(/detailedCombatId=(\d+)/i)[1];

        let battles = getItem('battles') || {};
        if ((typeof battles[this._rep_id] !== 'undefined') && (battles[this._rep_id].ikalogs)) {
            $('#ikalogs_already_exist', this.$el).show();
            $('#ikalogs_already_exist a', this.$el).attr('href', battles[this._rep_id].ikalogs);
        } else {
            $('#ikalogs_already_exist', this.$el).hide();
        }

        this.checkIsAuth();

        $('select[name="report_type"]', this.$el).change(() => {
            $('select[name="report_rounds"]', this.$el).hide();
            $('.ikalogs_between', this.$el).hide();

            switch($('select[name="report_type"]', this.$el).val()) {
                case 'each' :
                    $('select[name="report_rounds"]', this.$el).show();
                    break;

                case "between" :
                    $('.ikalogs_between', this.$el).show();
                    break;
            }
        });

        $('a.button', this.$el).click((e) => {
            e.preventDefault();
            this.analize();
        });
    }

    async analize() {
        this._rounds = [];
        this._short = null;
        this._full = {};
        this._users_ally = {};

        let reportType = $('select[name="report_type"]', this.$el).val();
        $('.ikalogs_result, .ikalogs_block', this.$el).hide();
        $('.ikalogs_loader', this.$el).show();
        $('.ikalogs_loader span', this.$el).text(LANGUAGE.getLocalizedString('ikalogs_get_info'));

        if (reportType === 'between') {
            let _rounds = $('.ikalogs_between input', this.$el).val().trim();
            if (!_rounds) {
                $('select[name="report_type"]', this.$el).val('short');
                reportType = 'short';
            }

            _rounds = _rounds.replace(/\s/g, '').split(',');
            _.each(_rounds, (v) => {
                if (/^\d+$/.test(v)) {
                    this._rounds.push(parseInt(v));
                } else if(/^\d+-\d+$/.test(v)) {
                    let t = v.split('-');
                    if (parseInt(t[0]) < parseInt(t[1])) {
                        this.updateRounds(t[0], t[1]);
                    } else if (t[0] > t[1]) {
                        this.updateRounds(t[1], t[0]);
                    } else {
                        this._rounds.push(parseInt(t[0]));
                    }
                }
            });

            if(this._rounds.length === 0) {
                $('select[name="report_type"]', this.$el).val('short');
                reportType = 'short';
            } else {
                this._rounds = _.uniq(this._rounds);
                this._rounds = this._rounds.sort(function(a, b) {
                    return a - b;
                });
            }
        }

        let count = 2;
        let _afterAll = () => {
           if (--count === 0) {
               this.isComplete();
           }
        };

        let $troopsReport = $('#troopsReport');

        // Получаем краткий доклад
        this.getShortLog(_afterAll);

        $.get('/index.php?view=highscore&showMe=1&ajax=1', (data) => {
            data = data[1][1][1];
            let t = data.match(/<tr class="(.*?\s)?own(\s.*?)?">[\s\S]*?<td class="name">([^<]+)<\/td>[\s\S]*?<td class="allytag">([\s\S]*?)<\/td>/);
            if (t) {
                this._users_ally[t[3].trim()] = t[4].replace(/(<[^>]+>)/g, '').trim();
            }

            _afterAll();
        }, 'json');


        if (reportType !== 'short') {
            $('.ikalogs_loader span', this.$el).text(LANGUAGE.getLocalizedString('ikalogs_get_rounds'));
            count++;
            this.getDetailRound($troopsReport.find('.contentBox01h p.link a').eq(0).attr('href').match(/combatRound=(\d+)/i)[1], (rep, data) => {
                if (!rep) {
                    _afterAll();
                    return;
                }

                this._all_rounds = getInt(rep.match(/\d+ \/ (\d+)<\//i)[1]);

                switch (reportType) {
                    case 'each' :
                        this.getRoundList(parseInt($('select[name="report_rounds"]', this.$el).val()));
                        break;

                    case "full" :
                        this.getRoundList(1);
                        break;

                    case "last" :
                        this._rounds = [ this._all_rounds ];
                        break;
                }

                if (this._rounds[0] === 1) {
                    this.setFullRound(rep);
                }


                let queue = [].concat(this._rounds);
                let $ikalogsProgress = $('.ikalogs_loader_progress div', this.$el);
                let queryFunc = () => {
                    if (!queue.length) {
                        return;
                    }

                    let r_id = queue.shift();
                    count++;
                    this.getDetailRound(r_id, (rep, data) => {
                        if (rep) {
                            this.setFullRound(rep);
                        }

                        queryFunc();
                        _afterAll();

                        $($ikalogsProgress).width((100 - ((queue.length * 100) / this._rounds.length)) + '%');
                    });
                };

                for (let i = 0; i < 5; i++) {
                    queryFunc();
                }

                _afterAll();
            });
        }
    }

    isComplete() {
        $('.ikalogs_loader span', this.$el).text(LANGUAGE.getLocalizedString('ikalogs_saving'));
        this.sendReport();
    }

    sendReport() {
        let version = $('#GF_toolbar .version').text().replace(/[^\d.]+/g, '').split('.');
        if (version.length === 3) { version.push('0'); }

        let obj = {
            'short'    : this._short,
            'server'   : getServerDomain(),
            'world'    : getServerWorld().substring(1),
            'rounds'   : this._rounds,
            'max'      : this._all_rounds,
            'ally'     : this._users_ally,
            'rep_id'   : this._rep_id,
            'version'  : parseInt(version.join('')),
            'user'     : this._user_id,
            'finished' : 0
        };

        $('.ikalogs_loader_progress div', this.$el).width('0%');
        if(this._rounds.length > CNT_ROUND_FOR_PART) {
            this.sendPartLogs(obj, 0);
        } else {
            obj.full = this._full;
            obj.finished = 1;

            this.sendRequest(obj);
        }
    }

    getDetailRound(combatRound, callback) {
        $.get(`?view=militaryAdvisorDetailedReportView&combatRound=${combatRound}&detailedCombatId=${this._rep_id}&ajax=1`, (data) => {
            data = data[1][1][1];
            let matches = data.match(/id="mainview"([\s\S]*?)$/i);
            let result = (matches) ? ((matches.length === 2) ? matches[1] : matches[0]) : '';
            callback(result, data);
        }, 'json');
    }

    getShortLog(callback) {
        $.get(`?view=militaryAdvisorReportView&combatId=${this._rep_id}&ajax=1`, (data) => {
            data = data[1][1][1];
            this._short = data.match(/<div id="militaryAdvisorReportView">([\s\S]*?)$/)[1];
            callback();
        }, 'json');
    }

    sendPartLogs(obj, start, id) {
        $('.ikalogs_loader_progress div', this.$el).width((start * 100 / this._rounds.length) + '%');
        obj.full = {};

        if ((id) && (start > 0)) {
            obj.id = id;
        }

        if (start < obj.rounds.length) {
            if (start === CNT_ROUND_FOR_PART) {
                obj.short = 'incomplete';
            }

            for(let j = 1; j <= CNT_ROUND_FOR_PART; j++) {
                obj.full[obj.rounds[obj.rounds.length - (start + j)]] = this._full[obj.rounds[obj.rounds.length - (start + j)]];
            }
        } else {
            obj.finished = 1;
            this.sendRequest(obj);

            return;
        }

        this.sendRequest(obj, (data) => {
            if ((data) && (data.status === 'ok') && (data.rep_id)) {
                this.sendPartLogs(obj, start + CNT_ROUND_FOR_PART, data.rep_id);
            } else {
                this.showFailed(data);
            }
        });
    }

    sendRequest(vars, callback) {
        chrome.runtime.sendMessage({ cmd: 'ajax', url: 'common/import/', method: 'post', body: vars }, (data) => {
            if (!data.status) {
                data = {'status': 'failed'};
            }

            if (!callback) {
                this.getResponse(data);
            } else {
                callback(data);
            }
        });
    }

    getResponse(data) {
        $('.ikalogs_block', this.$el).hide();
        $('.ikalogs_loader', this.$el).hide();
        $('.ikalogs_result', this.$el).show();

        if ((data) && (data.status === 'ok')) {
            $('.ikalogs_result span', this.$el).removeClass('ikalogs_failed').text(LANGUAGE.getLocalizedString('ikalogs_saving_success'));
            $('.ikalogs_result a', this.$el).off('click').text(LANGUAGE.getLocalizedString('ikalogs_open_report')).attr('href', data.url);

            let battles = getItem('battles') || {};
            battles[this._rep_id] = {
                ikalogs: data.url,
                date: _.trim($('#troopsReport h3.header .date').text().trim(), '()')
            };

            setItem('battles', battles);
        } else {
            this.showFailed(data);
        }
    }

    updateRounds(a, b) {
        a = parseInt(a);
        b = parseInt(b);
        for(let i = a; i <= b; i++) {
            this._rounds.push(i);
        }
    }

    getRoundList(step) {
        for(let i = 1; i <= this._all_rounds; i += step) {
            this._rounds.push(i);
        }
    }

    setFullRound(data) {
        let rounds = data.match(/(\d+) \/ (\d+)<\//i);

        if (rounds.length === 3) {
            let round = parseInt(rounds[1]);
            if (this._rounds.indexOf(round) > -1) {
                this._full[round] = data;
            }
        }
    }

    checkIsAuth() {
        IkalogsRu.checkIsAuth((email) => {
            if (email) {
                $('#ikalogs_auth').text(email);
            } else {
                $('#ikalogs_auth').html(`<a href="https://ikalogs.ru" target="__blank" class="ikalogs_not_logged">${LANGUAGE.getLocalizedString('ikalogs_not_logged')}</a>`);
            }
        });
    }

    showFailed(data) {
        console.error('Ikalogs ErrorNo:', data.errorNo);

        $('.ikalogs_result span', this.$el).addClass('ikalogs_failed').html(LANGUAGE.getLocalizedString('ikalogs_saving_failed'));
        $('.ikalogs_result a', this.$el).text(LANGUAGE.getLocalizedString('ikalogs_repeat')).one('click', this.repeat.bind(this));
    }

    repeat(e) {
        e.preventDefault();
        this.analize();
    }
}

export default Ikalogs;
