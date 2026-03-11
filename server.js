const express = require('express');
const cors    = require('cors');
const path    = require('path');
const fetch   = (...args) => import('node-fetch').then(({ default: f }) => f(...args));

const app  = express();
const PORT = process.env.PORT || 3000;

const API_SECRET = process.env.API_SECRET || null;

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname)));

// ── Auth ──────────────────────────────────────────────────────────────────────
function checkSecret(req, res, next) {
  if (!API_SECRET) return next();
  const provided = req.headers['x-api-secret'] || req.query.secret;
  if (provided !== API_SECRET) return res.status(401).json({ error: 'Unauthorized' });
  next();
}

// ── Trigger queue ─────────────────────────────────────────────────────────────
// surface.html calls POST /trigger → pushes { page, bank } onto the queue
// Python app calls GET /poll       → pops and returns the next item (or 204)
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

// ── Routes ────────────────────────────────────────────────────────────────────

// Index — surface control page (public, no login)
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'surface.html')));

// Health check
app.get('/health', (req, res) => res.json({
  status: 'ok',
  queued: triggerQueue.length,
  mode: 'osc-relay',
}));

// State — read by surface.html and admin.html
app.get('/state', (req, res) => res.json(appState));

// State — written by admin.html (requires secret)
app.put('/state', checkSecret, (req, res) => {
  const { buttons, views } = req.body;
  if (!Array.isArray(buttons) || typeof views !== 'object') {
    return res.status(400).json({ error: 'Expected { buttons: [], views: {} }' });
  }
  appState = { buttons, views };
  console.log(`[state] ${buttons.length} buttons, ${Object.keys(views).length} views`);
  res.json({ ok: true });
});

// Trigger — surface.html posts here; enqueues for Python app to pick up
// No auth: surface is public facing and has no login
app.post('/trigger', (req, res) => {
  const { page, bank } = req.body;
  if (!page || !bank) return res.status(400).json({ error: 'page and bank required' });

  triggerQueue.push({ page, bank, ts: Date.now() });
  console.log(`[queue] P${page}B${bank}  (queue depth: ${triggerQueue.length})`);
  res.json({ ok: true, queued: triggerQueue.length });
});

// Poll — Python bridge app calls this on a loop to dequeue triggers
// Requires secret (only the local Python app should call this)
app.get('/poll', checkSecret, (req, res) => {
  // Drop stale items older than 10 seconds (e.g. if Python was offline)
  const now = Date.now();
  while (triggerQueue.length && now - triggerQueue[0].ts > 10000) {
    console.log(`[queue] dropping stale trigger`);
    triggerQueue.shift();
  }

  if (triggerQueue.length === 0) {
    return res.status(204).end(); // nothing waiting
  }

  const item = triggerQueue.shift();
  console.log(`[poll]  dispatching P${item.page}B${item.bank}`);
  res.json({ page: item.page, bank: item.bank });
});

app.listen(PORT, () => {
  console.log(`Companion Bridge  :${PORT}`);
  console.log(`  /           → surface (public)`);
  console.log(`  /admin.html → admin (use secret)`);
  console.log(`  /poll       → Python OSC relay (use secret)`);
});
