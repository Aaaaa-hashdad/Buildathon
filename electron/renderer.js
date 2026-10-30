// Columns shown in the live table (Excel gets them all).
const TABLE_COLS = {
  people: ['Name', 'Headline', 'Location', 'Connection'],
  companies: ['Name', 'Industry / Location', 'Followers'],
  jobs: ['Title', 'Company', 'Location', 'Posted', 'Apply type'],
  boards: ['Title', 'Company', 'Location', 'Posted', 'Apply type'],
};

const $ = (id) => document.getElementById(id);
let columns = [], linkCol = '', lastFile = null, lastKind = null, outputDir = null, running = false, companiesFile = null;

// Career sites are one search type; LinkedIn has three (people, companies, jobs).
const source = () => document.querySelector('input[name=source]:checked').value;
const currentKind = () => (source() === 'boards' ? 'boards' : $('kind').value);

function setHead(kind) {
  $('head').innerHTML = '';
  for (const c of TABLE_COLS[kind]) {
    const th = document.createElement('th');
    th.textContent = c;
    $('head').appendChild(th);
  }
  $('rows').innerHTML = '';
}

function log(text) {
  $('log').append(text + '\n');
  $('log').scrollTop = $('log').scrollHeight;
}

// Rows can arrive hundreds at a time (career sites), so they're drawn once per frame, not one by one.
let pendingRows = [];
function queueRow(values) {
  pendingRows.push(values);
  if (pendingRows.length === 1) requestAnimationFrame(flushRows);
}
function flushRows() {
  if (!pendingRows.length) return;
  const cols = TABLE_COLS[currentKind()], frag = document.createDocumentFragment();
  const flash = pendingRows.length < 10;     // a flash on 300 rows at once is just noise
  for (const values of pendingRows) {
    const row = Object.fromEntries(columns.map((c, i) => [c, values[i] ?? '']));
    const tr = document.createElement('tr');
    if (flash) tr.className = 'fresh';
    tr.dataset.link = row[linkCol] || '';
    for (const c of cols) {
      const td = document.createElement('td');
      td.textContent = td.title = row[c] || '';
      tr.appendChild(td);
    }
    frag.appendChild(tr);
  }
  pendingRows = [];
  $('rows').appendChild(frag);
  const wrap = $('rows').closest('.table-wrap');
  wrap.scrollTop = wrap.scrollHeight;
}
$('rows').addEventListener('dblclick', (e) => {
  const tr = e.target.closest('tr');
  if (tr && tr.dataset.link) window.bot.openUrl(tr.dataset.link);
});

// Chrome light: only checked while LinkedIn is the chosen source.
setInterval(() => { if (source() === 'linkedin') window.bot.send({ cmd: 'chrome_status' }); }, 3000);

function setRunning(on) {
  running = on;
  $('start').disabled = on;
  $('stop').disabled = !on;
  for (const r of document.querySelectorAll('input[name=source]')) r.disabled = on;
}

function showKind() {
  const linkedin = source() === 'linkedin';
  for (const id of ['chromeCard', 'chrome', 'kindRow', 'pagesRow', 'speedRow']) $(id).hidden = !linkedin;
  $('companiesCard').hidden = linkedin;
  $('boardsHint').hidden = linkedin;
  $('grabRow').hidden = !(linkedin && $('kind').value === 'jobs');
  $('linkedinImport').hidden = !(linkedin && $('kind').value === 'jobs');
  window.bot.send({ cmd: linkedin ? 'chrome_status' : 'boards_info' });
  try { localStorage.setItem('source', source()); } catch { /* storage off: just don't remember */ }
  if (!running) setHead(currentKind());
}
$('kind').addEventListener('change', showKind);
for (const r of document.querySelectorAll('input[name=source]')) r.addEventListener('change', showKind);

function addCompany() {
  const text = $('boardsAdd').value.trim();
  if (!text) { $('boardsAdd').focus(); return; }
  window.bot.send({ cmd: 'boards_add', text });
  $('boardsAdd').value = '';
}
$('boardsAddBtn').onclick = addCompany;
$('boardsImport').onclick = () => {
  $('boardsImport').disabled = true;            // one import at a time; re-enabled when the list comes back
  $('boardsImport').textContent = 'Importing…';
  window.bot.send({ cmd: 'boards_import' });
};
$('boardsEdit').onclick = () => companiesFile && window.bot.openPath(companiesFile);

// AI assistants: connect the bot's MCP server to Claude on this PC.
let mcpConfig = '';
const MCP_TEXT = {
  'connected': ['Connected', 'ok'],
  'needs update': ['Connected to an old folder path: connect again', 'warn-text'],
  'not connected': ['Not connected', ''],
  'not installed': ['Not installed on this PC', ''],
};
function showMcp(id, state) {
  const [text, cls] = MCP_TEXT[state] || [state, ''];
  $(id).textContent = text;
  $(id).className = 'hint ' + cls;
  $(id + 'Btn').disabled = state === 'not installed';
  $(id + 'Btn').textContent = state === 'connected' ? 'Reconnect' : 'Connect';
}
$('mcpDesktopBtn').onclick = () => { $('mcpDesktopBtn').disabled = true; window.bot.send({ cmd: 'mcp_connect_desktop' }); };
$('mcpCodeBtn').onclick = () => { $('mcpCodeBtn').disabled = true; window.bot.send({ cmd: 'mcp_connect_code' }); };
$('mcpCopy').onclick = async () => {
  if (!mcpConfig) return;
  try {
    await navigator.clipboard.writeText(mcpConfig);
    log('MCP settings copied. Paste them into the other app\'s MCP config.');
  } catch {
    log('Copy didn\'t work here; these are the settings:\n' + mcpConfig);
  }
};

// Jobs (LinkedIn or career sites) go to the Superhuman Docs tracker. The first click also sets the doc up.
const canTrack = () => lastFile && (lastKind === 'jobs' || lastKind === 'boards');
let trackerLink = '';
$('superhuman').onclick = () => {
  if (!canTrack()) return;
  $('superhuman').disabled = true;
  $('superhuman').textContent = 'Sending…';
  window.bot.send({ cmd: 'superhuman_send', file: lastFile });
};

// After a LinkedIn jobs search: send that search's companies to the career-site list.
$('linkedinImport').onclick = () => {
  if (!lastFile || lastKind !== 'jobs') return;
  $('linkedinImport').disabled = true;
  $('linkedinImport').textContent = 'Finding companies…';
  log('--- looking up the companies from this search on the career sites ---');
  window.bot.send({ cmd: 'boards_import', file: lastFile });
};

$('openChrome').onclick = () => window.bot.send({ cmd: 'open_chrome' });
$('stop').onclick = () => window.bot.send({ cmd: 'stop' });
$('folder').onclick = () => outputDir && window.bot.openPath(outputDir);
$('excel').onclick = () => lastFile && window.bot.openPath(lastFile);

function start() {
  if (running) return;
  const query = $('query').value.trim();
  const kind = currentKind();
  if (!query && kind !== 'boards') { log("Type something in 'Search for' first."); $('query').focus(); return; }
  const speed = document.querySelector('input[name=speed]:checked').value;
  const msg = {
    cmd: 'start', kind, query, speed,
    location: $('location').value.trim(),
    pages: Math.max(1, Math.min(10, parseInt($('pages').value, 10) || 3)),
    grab_links: kind === 'jobs' && $('grab').checked,
  };
  setHead(kind);
  setRunning(true);
  lastKind = kind;
  $('excel').disabled = true;
  $('linkedinImport').disabled = true;
  $('superhuman').disabled = true;
  log(kind === 'boards'
    ? `--- career sites: '${query || 'all jobs'}' ${msg.location} ---`
    : `--- LinkedIn ${kind}: '${query}' ${msg.location} (${msg.pages} pages, ${speed} speed) ---`);
  window.bot.send(msg);
}
$('start').onclick = start;
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' || e.target.tagName !== 'INPUT') return;
  if (e.target.id === 'boardsAdd') addCompany(); else start();
});

window.bot.onEvent((ev) => {
  switch (ev.type) {
    case 'ready':
      outputDir = ev.output_dir;
      window.bot.send({ cmd: source() === 'linkedin' ? 'chrome_status' : 'boards_info' });   // Python can start after the window asked
      window.bot.send({ cmd: 'mcp_status' });
      break;
    case 'chrome':
      $('chrome').textContent = ev.connected ? 'Chrome connected' : 'Chrome not open';
      $('chrome').className = 'pill ' + (ev.connected ? 'on' : 'off');
      break;
    case 'columns': columns = ev.columns; linkCol = ev.link; break;
    case 'mcp':
      mcpConfig = ev.config;
      showMcp('mcpDesktop', ev.claude_desktop);
      showMcp('mcpCode', ev.claude_code);
      break;
    case 'superhuman':
      trackerLink = ev.doc_link || '';
      $('superhuman').textContent = 'Send to Superhuman Docs';
      $('superhuman').disabled = !canTrack();
      if (trackerLink) log(`Tracker: ${trackerLink}`);
      break;
    case 'boards':
      companiesFile = ev.file;
      $('boardsImport').disabled = false;
      $('boardsImport').textContent = 'Import companies from LinkedIn results';
      $('linkedinImport').textContent = 'Add these companies to career sites';
      $('linkedinImport').disabled = !(lastFile && lastKind === 'jobs');
      $('boardsCount').textContent = ev.count ? `${ev.count} companies on the list` : 'No companies yet';
      $('boardsPlatforms').textContent = Object.entries(ev.by_platform).map(([p, n]) => `${p} ${n}`).join(' · ');
      break;
    case 'log': log(ev.text); break;
    case 'status':
      $('now').textContent = '🤖 ' + ev.value;
      $('now').classList.remove('idle');
      break;
    case 'row': queueRow(ev.value); break;
    case 'done':
      flushRows();
      setRunning(false);
      $('now').textContent = ev.path ? `Done – ${$('rows').children.length} saved` : 'Idle';
      $('now').classList.add('idle');
      if (ev.path) {
        lastFile = ev.path;
        $('excel').disabled = false;
        log("Done! Click 'Open Excel'.");
        $('superhuman').disabled = !canTrack();
        if (lastKind === 'jobs') {
          $('linkedinImport').disabled = false;
          log("Click 'Add these companies to career sites' to also follow them without LinkedIn.");
        }
      }
      break;
  }
});

try {
  const saved = localStorage.getItem('source');
  if (saved) document.querySelector(`input[name=source][value="${saved}"]`).checked = true;
} catch { /* first run or storage off: career sites */ }
showKind();
