import Win from '../../helper/win.js';
import Parent from './dummy.js';
import db from '../../libs/db.js';
import { getThisKey, execute_js } from '../../utils.js';

class Module extends Parent {
    async init() {
        $('#GF_toolbar li.notes').data('ikaeasy', true);
        await this.initDB();


        let $a = $('#GF_toolbar li.notes a');
        this.title = $a.text().trim();
        $a.remove();

        $a = $(`<a href="">${this.title}</a>`);
        $('#GF_toolbar li.notes').append($a);
        $a.click((e) => {
            e.preventDefault();
            this.open();
        });

        this.win = null;
    }

    async initDB() {
        const s = await db.open({
            server: getThisKey('main'),
            version: 1,
            schema: {
                notes: {
                    key: {keyPath: 'id', autoIncrement: true}
                }
            }
        });

        this.db = s;
    }

    updated() {

    }

    getDefaultNote() {
        return new Promise((resolve) => {
            if (typeof this._system_note !== 'undefined') {
                resolve(this._system_note);
                return;
            }

            let sideBarExt = '';
            if (localStorage.sideBarExt) {
                sideBarExt = `&sideBarExt=${localStorage.sideBarExt}`;
            }

            let url = `/index.php?view=avatarNotes${sideBarExt}&actionRequest=${Front.data.actionRequest}&ajax=1`;
            $.get(url, (data) => {
                let html = data[1][1][1];
                let m = html.match(/<textarea[\s\S]*?>([\s\S]*?)<\/textarea>/m);
                if (m) {
                    this._system_note = m[1];
                } else {
                    this._system_note = null;
                }

                resolve(this._system_note);
            }, 'json');
        });
    }

    open() {
        if (!this.db) {
            setTimeout(() => {
                this.open();
            }, 100);
            return;
        }

        if (this.win) {
            this.win.remove();
            return;
        }

        this.win = new Win({title: this.title, mainClass: 'ikaeasy-notes-window'}, true);

        this.win.on('close', () => {
            this.win = null;
        });

        this.win.on('ready', () => {
            this.rebuild();
        });
    }

    async rebuild(id = 0) {
        let [p1, p2] = await Promise.all([ this.getDefaultNote(), this.db.notes.query().all().execute() ]);

        let list = [];
        if (p1) {
            list.push({id: -1, title: LANGUAGE.getLocalizedString('note.ikariam_note'), text: p1, system: true});
        }

        list = list.concat(p2);
        const tpl = await this.render('dummy/notes/note-main', {list: list});
        const $el = $(tpl);
        this.win.getContent().empty().append($el);

        $el.find('.ikaeasy-notes-list-content > div').click((e) => {
            let $div = $(e.currentTarget);
            if (!$div.hasClass('ikaeasy-notes-list-active')) {
                this.changeNote($el, parseInt($div.data('id')), list);
            }
        });

        $el.find('.js-ikaeasy-notes-create').click((e) => {
            e.preventDefault();
            this.changeNote($el, null, list);
        });

        this.changeNote($el, id, list);
    }

    async changeNote($content, id, list) {
        if ((id !== null) && (!list.length)) {
            id = null;
        }

        let note = null;
        if (id === null) {
            // Создается новая
            $content.find('.ikaeasy-notes-list-content > div.ikaeasy-notes-list-active').removeClass('ikaeasy-notes-list-active');
        } else {
            if (id === 0) {
                $content.find('.ikaeasy-notes-list-content > div:first').addClass('ikaeasy-notes-list-active').siblings().removeClass('ikaeasy-notes-list-active');
                note = list[0];
            } else {
                $content.find(`.ikaeasy-notes-list-content > div[data-id="${id}"]`).addClass('ikaeasy-notes-list-active').siblings().removeClass('ikaeasy-notes-list-active');
                note = _.find(list, {id: id});
            }
        }

        const tpl = await this.render('dummy/notes/note-text', {note: note || {}});
        let $el = $(tpl);
        $content.find('.ikaeasy-notes-text').empty().append($el);

        $el.find('.button').click((e) => {
            e.preventDefault();

            if (note && note.system) {
                execute_js(`BubbleTips.bindBubbleTip(5, 11, "${LANGUAGE.getLocalizedString('note.change_ikariam_note')}")`);

                setTimeout(() =>{
                    let $tip = $('body > .bubble_tip');
                    let left = parseInt($tip.css('left'));
                    $tip.css('left', left - 150);

                    console.log(left)
                }, 50);

                return;
            }

            if ($(e.currentTarget).data('action') === 'delete') {
                if (confirm(LANGUAGE.getLocalizedString('note.delete_note_confirm'))) {
                    this.db.notes.remove(id).then(async () => {
                        this.rebuild();
                    });
                }
                return;
            }

            if ($(e.currentTarget).data('action') === 'save') {
                let title = $el.find('input').val().trim();
                let text = $el.find('textarea').val().trim();

                let data = {
                    title: title,
                    text: text,
                    updated: _.now()
                };

                if (note) {
                    data.id = note.id;
                }

                this.db.notes.update(data).then((note) => {
                    this.rebuild(note[0].id);
                });
            }
        });
    }
}

export default Module;
