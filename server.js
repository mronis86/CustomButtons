const express = require('express');
const cors    = require('cors');
const path    = require('path');

const app  = express();
const PORT = process.env.PORT || 3000;

const API_SECRET = process.env.API_SECRET || null;

app.use(cors());
app.use(express.json());

// ── Auth ──────────────────────────────────────────────────────────────────────
function checkSecret(req, res, next) {
  if (!API_SECRET) return next();
  const provided = req.headers['x-api-secret'] || req.query.secret;
  if (provided !== API_SECRET) return res.status(401).json({ error: 'Unauthorized' });
  next();
}

// ── Button location helpers (Companion 3.x page/row/col) ───────────────────────
// Legacy Companion 2.x used 1-based "bank" on an 8-wide grid.
// Convert bank → row/col when loading or saving old button configs.
function bankToRowCol(bank, cols = 8) {
  const i = Number(bank) - 1;
  if (!Number.isFinite(i) || i < 0) return { row: 0, col: 0 };
  return { row: Math.floor(i / cols), col: i % cols };
}

function normalizeButton(btn) {
  if (!btn || typeof btn !== 'object') return btn;
  const out = { ...btn };
  out.page = Number(out.page);
  if (!Number.isFinite(out.page) || out.page < 1) out.page = 1;

  if (out.row === undefined && out.col === undefined && out.bank !== undefined) {
    const loc = bankToRowCol(out.bank);
    out.row = loc.row;
    out.col = loc.col;
  }

  out.row = Number(out.row);
  out.col = Number(out.col);
  if (!Number.isFinite(out.row)) out.row = 0;
  if (!Number.isFinite(out.col)) out.col = 0;

  delete out.bank;
  return out;
}

function normalizeState(state) {
  const buttons = Array.isArray(state?.buttons)
    ? state.buttons.map(normalizeButton)
    : [];
  const views = (state?.views && typeof state.views === 'object') ? state.views : {};
  const dashUrl = typeof state?.dashUrl === 'string' ? state.dashUrl.trim() : '';
  return { buttons, views, dashUrl };
}

// ── Trigger queue ─────────────────────────────────────────────────────────────
const triggerQueue = [];

// ── In-memory state ───────────────────────────────────────────────────────────
let appState = normalizeState({
  buttons: [
    { id: 'b1', label: 'CAM 1',  page: 1, row: 0, col: 0, color: '#e63946' },
    { id: 'b2', label: 'CAM 2',  page: 1, row: 0, col: 1, color: '#457b9d' },
    { id: 'b3', label: 'WIDE',   page: 1, row: 0, col: 2, color: '#52b788' },
    { id: 'b4', label: 'TITLES', page: 1, row: 1, col: 0, color: '#e9c46a' },
    { id: 'b5', label: 'BREAK',  page: 1, row: 1, col: 1, color: '#f4a261' },
    { id: 'b6', label: 'STREAM', page: 2, row: 0, col: 0, color: '#a855f7' },
  ],
  views: {
    'v1': { name: 'CAMERAS',  buttonIds: ['b1', 'b2', 'b3'] },
    'v2': { name: 'GRAPHICS', buttonIds: ['b4', 'b5'] },
    'v3': { name: 'ALL',      buttonIds: ['b1','b2','b3','b4','b5','b6'] },
  },
  dashUrl: '',
});

// ── API ROUTES (must come before static middleware) ───────────────────────────

app.get('/favicon.ico', (req, res) => res.status(204).end());

app.get('/health', (req, res) => res.json({
  status: 'ok',
  queued: triggerQueue.length,
  mode: 'osc-relay',
  osc: 'location', // Companion 3.x /location/{page}/{row}/{col}/press
}));

app.get('/state', (req, res) => {
  console.log('[state] GET');
  res.json(appState);
});

app.put('/state', checkSecret, (req, res) => {
  const { buttons, views, dashUrl } = req.body;
  if (!Array.isArray(buttons) || typeof views !== 'object') {
    return res.status(400).json({ error: 'Expected { buttons: [], views: {}, dashUrl?: string }' });
  }
  appState = normalizeState({ buttons, views, dashUrl });
  console.log(`[state] PUT — ${appState.buttons.length} buttons, ${Object.keys(appState.views).length} views, dashUrl=${appState.dashUrl ? 'set' : 'empty'}`);
  res.json({ ok: true });
});

app.post('/trigger', (req, res) => {
  let { page, row, col } = req.body;

  // Accept legacy { page, bank } payloads and convert
  if ((row === undefined || col === undefined) && req.body.bank !== undefined) {
    const loc = bankToRowCol(req.body.bank);
    row = loc.row;
    col = loc.col;
  }

  page = Number(page);
  row  = Number(row);
  col  = Number(col);

  if (!Number.isFinite(page) || !Number.isFinite(row) || !Number.isFinite(col)) {
    return res.status(400).json({ error: 'page, row and col required (Companion location)' });
  }

  triggerQueue.push({ page, row, col, ts: Date.now() });
  console.log(`[queue] P${page}/R${row}/C${col}  (depth: ${triggerQueue.length})`);
  res.json({ ok: true, queued: triggerQueue.length });
});

app.get('/poll', checkSecret, (req, res) => {
  const now = Date.now();
  while (triggerQueue.length && now - triggerQueue[0].ts > 10000) {
    triggerQueue.shift();
  }
  if (triggerQueue.length === 0) return res.status(204).end();
  const item = triggerQueue.shift();
  console.log(`[poll] dispatching P${item.page}/R${item.row}/C${item.col}`);
  res.json({ page: item.page, row: item.row, col: item.col });
});

// ── STATIC + HTML (after API routes) ─────────────────────────────────────────
app.get('/', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.sendFile(path.join(__dirname, 'surface.html'));
});

app.get('/admin.html', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.sendFile(path.join(__dirname, 'admin.html'));
});

app.get('/dashboard.html', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.sendFile(path.join(__dirname, 'dashboard.html'));
});

app.get('/dashboard', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.sendFile(path.join(__dirname, 'dashboard.html'));
});

// ── START ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`Companion Bridge running on port ${PORT}`);
  console.log(`  GET  /            → surface.html`);
  console.log(`  GET  /dashboard   → Companion buttons + Ross DashBoard`);
  console.log(`  GET  /state       → button layout`);
  console.log(`  PUT  /state       → save layout (secret required)`);
  console.log(`  POST /trigger     → queue a button press (page/row/col)`);
  console.log(`  GET  /poll        → dequeue for OSC /location/.../press`);
});
