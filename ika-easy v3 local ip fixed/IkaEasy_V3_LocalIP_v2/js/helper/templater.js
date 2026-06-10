import sandbox from '../sandbox.js';

const callbacks = {};

sandbox.on('message', (event) => {
  if (event.data.subType === 'tpl_ready' && callbacks[event.data.form.key]) {
    callbacks[event.data.form.key](event.data.form.result);
    delete callbacks[event.data.form.key];
  }
});

export default async function render(path, data = {}, helpers = {}) {
  // if path ends with .ejs, remove it
  if (path.endsWith('.ejs')) {
    path = path.slice(0, -4);
  }

  try {
    const { default: PrepareTpl } = await import(`../../tpl/${path}.js`);

    const prepareTpl = new PrepareTpl(data, helpers);
    data = prepareTpl.getData();
  } catch (error) {
    console.error(error);
  }

  return new Promise((resolve) => {
    const key = `${path}:${Date.now()}:${Math.random()}`;
    callbacks[key] = resolve;

    sandbox.send('tpl_render', {
      key,
      path,
      params: data
    }, '*');
  });
}
