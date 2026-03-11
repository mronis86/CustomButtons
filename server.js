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

// ── Trigger queue ─────────────────────────────────────────────────────────────
const triggerQueue = [];

// ── In-memory state ───────────────────────────────────────────────────────────
let appState = {
  buttons: [
    { id: 'b1', label: 'CAM 1',  page: 1, bank: 1, color: '#e63946' },
    { id: 'b2', label: 'CAM 2',  page: 1, bank: 2, color: '#457b9d' },
    { id: 'b3', label: 'WIDE',   page: 1, bank: 3, color: '#52b788' },
    { id: 'b4', label: 'TITLES', page: 1, bank: 4, color: '#e9c46a' },
    { id: 'b5', label: 'BREAK',  page: 1, bank: 5, color: '#f4a261' },
    { id: 'b6', label: 'STREAM', page: 2, bank: 1, color: '#a855f7' },
  ],
  views: {
    'v1': { name: 'CAMERAS',  buttonIds: ['b1', 'b2', 'b3'] },
    'v2': { name: 'GRAPHICS', buttonIds: ['b4', 'b5'] },
    'v3': { name: 'ALL',      buttonIds: ['b1','b2','b3','b4','b5','b6'] },
  },
};

// ── API ROUTES (must come before static middleware) ───────────────────────────

app.get('/favicon.ico', (req, res) => res.status(204).end());

app.get('/health', (req, res) => res.json({
  status: 'ok',
  queued: triggerQueue.length,
  mode: 'osc-relay',
}));

app.get('/state', (req, res) => {
  console.log('[state] GET');
  res.json(appState);
});

app.put('/state', checkSecret, (req, res) => {
  const { buttons, views } = req.body;
  if (!Array.isArray(buttons) || typeof views !== 'object') {
    return res.status(400).json({ error: 'Expected { buttons: [], views: {} }' });
  }
  appState = { buttons, views };
  console.log(`[state] PUT — ${buttons.length} buttons, ${Object.keys(views).length} views`);
  res.json({ ok: true });
});

app.post('/trigger', (req, res) => {
  const { page, bank } = req.body;
  if (!page || !bank) return res.status(400).json({ error: 'page and bank required' });
  triggerQueue.push({ page, bank, ts: Date.now() });
  console.log(`[queue] P${page}B${bank}  (depth: ${triggerQueue.length})`);
  res.json({ ok: true, queued: triggerQueue.length });
});

app.get('/poll', checkSecret, (req, res) => {
  const now = Date.now();
  while (triggerQueue.length && now - triggerQueue[0].ts > 10000) {
    triggerQueue.shift();
  }
  if (triggerQueue.length === 0) return res.status(204).end();
  const item = triggerQueue.shift();
  console.log(`[poll] dispatching P${item.page}B${item.bank}`);
  res.json({ page: item.page, bank: item.bank });
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

// ── START ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`Companion Bridge running on port ${PORT}`);
  console.log(`  GET  /        → surface.html`);
  console.log(`  GET  /state   → button layout`);
  console.log(`  PUT  /state   → save layout (secret required)`);
  console.log(`  POST /trigger → queue a button press`);
  console.log(`  GET  /poll    → dequeue for OSC relay (secret required)`);
});
