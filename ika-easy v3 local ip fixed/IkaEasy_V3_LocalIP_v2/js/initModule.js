import sandbox from './sandbox.js';

sandbox.on('ready', async () => {
  await import('./langs.js');
  await import('./navigation.js');
})
