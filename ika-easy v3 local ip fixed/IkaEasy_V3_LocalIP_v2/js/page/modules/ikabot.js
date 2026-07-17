import Win from '../../helper/win.js';
import Parent from './dummy.js';
import { addToLeftMenu } from '../../utils.js';

class Module extends Parent {
    init() {
        if (!$('.ikaeasy-ikabot').length) {
            this.addLeftMenu();
        }
    }

    async addLeftMenu() {
        const $li = await addToLeftMenu('image_empire', 'ikabot', false, this.options.get('quick_menu'));
        $li.addClass('ikaeasy-ikabot');
        $li.click(() => {
            this.toggle();
        });
    }

    toggle(b) {
        if (typeof b !== 'boolean') {
            b = !this._window;
        }

        if (b) {
            if (!this._window) {
                this.createWindow();
            }
        } else {
            this._window && this._window.remove();
        }
    }

    createWindow() {
        this._window = new Win({ title: 'ikabot', width: '70%' });

        this._window.on('close', () => {
            this._window = null;
        });

        this._window.on('ready', () => {
            this.$content = this._window.getContent();
            this.$content.html('<div style="padding:10px;">Loading ikabot data…</div>');
            this.load();
        });
    }

    async load() {
        let data;
        try {
            const resp = await fetch(`${location.origin}/index.php?ikabot=1&action=ikaeasy`, { credentials: 'include' });
            data = await resp.json();
        } catch (e) {
            this.$content && this.$content.html(
                '<div style="padding:10px;">ikabot web server not detected.<br/><br/>' +
                'Open this game through the ikabot web server (menu option 16) to see live task and construction data here.</div>'
            );
            return;
        }

        if (!this.$content) {
            return;
        }

        this.$content.empty().append(this.renderProcesses(data.processes || []));
        this.$content.append(this.renderConstruction(data.construction || {}));
    }

    renderProcesses(processes) {
        let rows = '';
        processes.forEach((p) => {
            let date = p.date ? moment.unix(p.date).format('YYYY-MM-DD HH:mm') : '';
            rows += `<tr><td>${this.esc(p.action)}</td><td>${this.esc(p.status)}</td><td>${date}</td></tr>`;
        });

        if (!rows) {
            rows = '<tr><td colspan="3">No ikabot tasks running.</td></tr>';
        }

        return $(
            '<div style="padding:10px;"><b>Running tasks</b>' +
            '<table class="table01" style="width:100%;margin-top:5px;">' +
            '<tr><th>Task</th><th>Status</th><th>Started</th></tr>' +
            rows + '</table></div>'
        );
    }

    renderConstruction(construction) {
        let blocks = '';
        Object.keys(construction).forEach((cityId) => {
            let items = construction[cityId] || [];
            if (!items.length) {
                return;
            }

            let rows = '';
            items.forEach((b) => {
                let finish = b.expected_finish ? moment.unix(b.expected_finish).format('YYYY-MM-DD HH:mm') : '';
                rows += `<tr><td>${this.esc(b.building)}</td><td>${this.esc(b.target_level)}</td>` +
                    `<td>${this.esc(b.status)}</td><td>${finish}</td></tr>`;
            });

            let name = this.esc(items[0].city_name || cityId);
            blocks += `<b>${name}</b><table class="table01" style="width:100%;margin:5px 0 12px;">` +
                '<tr><th>Building</th><th>Target</th><th>Status</th><th>ETA</th></tr>' + rows + '</table>';
        });

        if (!blocks) {
            blocks = 'No buildings queued in Construction Manager.';
        }

        return $(`<div style="padding:10px;"><b>Construction queue</b><div style="margin-top:5px;">${blocks}</div></div>`);
    }

    esc(v) {
        return $('<div>').text(v === null || typeof v === 'undefined' ? '' : v).html();
    }

    destroy() {
        this._window && this._window.remove();
    }
}

export default Module;
