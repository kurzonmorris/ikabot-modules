import { MSG } from '../lib/constants.js';

const $ = (selector) => document.querySelector(selector);

function send(type, payload = {}) {
  return chrome.runtime.sendMessage({ type, ...payload });
}

// --- connections -------------------------------------------------------------

function setConn(which, state, detail) {
  const root = $(`#conn-${which}`);
  root.querySelector('.dot').dataset.state = state;
  root.querySelector('.detail').textContent = detail;
}

async function testConnection(which) {
  setConn(which, 'busy', 'checking…');
  const message = which === 'vinted' ? MSG.TEST_VINTED : MSG.TEST_SHOPIFY;
  const response = await send(message);
  if (response?.ok) {
    const r = response.result;
    setConn(which, 'ok', which === 'vinted'
      ? `signed in as ${r.username || r.userId}`
      : `${r.shop} · ${r.currency}`);
  } else {
    setConn(which, 'error', response?.error ?? 'failed');
  }
}

// --- run state ---------------------------------------------------------------

function renderState(state) {
  const running = Boolean(state?.running);
  $('#run').disabled = running;
  $('#cancel').disabled = !running;

  if (state?.error) {
    $('#status').textContent = state.error;
    $('#status').dataset.tone = 'error';
    return;
  }

  const progress = state?.progress;
  const detail = progress?.total ? ` (${progress.index}/${progress.total})` : '';
  $('#status').textContent = running ? `${state.phase}${detail}…` : 'Idle';
  $('#status').dataset.tone = '';
}

function renderLastRun(lastRun) {
  const root = $('#last-run');
  if (!lastRun) {
    root.textContent = 'No runs yet.';
    return;
  }

  const { summary, results, dryRun, finishedAt, counts } = lastRun;
  root.innerHTML = '';

  const grid = document.createElement('div');
  grid.className = 'summary-grid';
  for (const [label, value] of [
    ['matched', summary.matched],
    ['vinted only', summary.vintedOnly],
    ['shopify only', summary.shopifyOnly],
    [dryRun ? 'would apply' : 'applied', dryRun ? results.skipped : results.applied],
    ['failed', results.failed],
    ['actions', summary.actions],
  ]) {
    const cell = document.createElement('div');
    cell.className = 'stat';
    cell.innerHTML = '<b></b><span></span>';
    cell.querySelector('b').textContent = value ?? 0;
    cell.querySelector('span').textContent = label;
    grid.append(cell);
  }
  root.append(grid);

  const note = document.createElement('div');
  note.className = 'muted';
  note.textContent =
    `${dryRun ? 'Dry run' : 'Live run'} · ${new Date(finishedAt).toLocaleString()} · ` +
    `${counts.vinted} Vinted / ${counts.shopify} Shopify items`;
  root.append(note);
}

function addLogLine(entry) {
  const list = $('#log');
  const li = document.createElement('li');
  li.dataset.level = entry.level;
  const time = document.createElement('time');
  time.textContent = new Date(entry.at).toLocaleTimeString();
  li.append(time, document.createTextNode(entry.message));
  list.append(li);
  while (list.children.length > 200) list.firstChild.remove();
  list.scrollTop = list.scrollHeight;
}

async function loadLogs() {
  const { logs = [] } = await send(MSG.GET_LOGS);
  $('#log').innerHTML = '';
  logs.slice(-100).forEach(addLogLine);
}

// --- wiring ------------------------------------------------------------------

async function init() {
  const response = await send(MSG.GET_STATE);
  if (response?.ok) {
    $('#dry-run').checked = response.settings.sync.dryRun;
    renderState(response.state);
    renderLastRun(response.lastRun);
    if (!response.settings.shopify.shopDomain) {
      setConn('shopify', 'error', 'not configured — open Settings');
    }
    setConn('vinted', 'unknown', response.settings.vinted.domain);
  }
  await loadLogs();
}

document.addEventListener('click', async (event) => {
  const testTarget = event.target.dataset?.test;
  if (testTarget) return testConnection(testTarget);

  switch (event.target.id) {
    case 'open-options':
      event.preventDefault();
      return chrome.runtime.openOptionsPage();

    case 'run': {
      const response = await send(MSG.RUN_SYNC, { dryRun: $('#dry-run').checked });
      if (response?.ok) renderLastRun(response.lastRun);
      else renderState({ running: false, error: response?.error });
      return;
    }

    case 'cancel':
      return send(MSG.CANCEL_SYNC);

    case 'clear-logs':
      await send(MSG.CLEAR_LOGS);
      $('#log').innerHTML = '';
      return;

    case 'export': {
      // Saved via an anchor rather than chrome.downloads, so the extension does
      // not have to ask for the downloads permission.
      const { report } = await send(MSG.EXPORT_REPORT);
      const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `vinted-shopify-report-${Date.now()}.json`;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      return;
    }
    default:
  }
});

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === MSG.STATE_CHANGED) renderState(message.state);
  if (message?.type === MSG.LOG_APPENDED) addLogLine(message.entry);
});

init();
