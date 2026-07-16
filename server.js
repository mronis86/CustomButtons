const express = require('express');
const cors    = require('cors');
const path    = require('path');
const fs      = require('fs');
const zlib    = require('zlib');
const crc32   = require('zlib').crc32;

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Minimal ZIP builder (no extra npm deps) ───────────────────────────────────
function u16(n) {
  const b = Buffer.alloc(2);
  b.writeUInt16LE(n >>> 0, 0);
  return b;
}
function u32(n) {
  const b = Buffer.alloc(4);
  b.writeUInt32LE(n >>> 0, 0);
  return b;
}
function zipCrc32(buf) {
  // Node zlib.crc32 available in Node 22+; fall back for older engines
  if (typeof crc32 === 'function') return crc32(buf) >>> 0;
  let c = ~0;
  for (let i = 0; i < buf.length; i++) {
    c ^= buf[i];
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return (~c) >>> 0;
}
function buildZip(files) {
  const locals = [];
  const centrals = [];
  let offset = 0;

  for (const file of files) {
    const name = Buffer.from(file.name, 'utf8');
    const data = Buffer.isBuffer(file.data) ? file.data : Buffer.from(file.data);
    const compressed = zlib.deflateRawSync(data);
    const crc = zipCrc32(data);

    const local = Buffer.concat([
      u32(0x04034b50),
      u16(20), u16(0), u16(8),
      u16(0), u16(0),
      u32(crc),
      u32(compressed.length),
      u32(data.length),
      u16(name.length), u16(0),
      name,
      compressed,
    ]);

    const central = Buffer.concat([
      u32(0x02014b50),
      u16(20), u16(20), u16(0), u16(8),
      u16(0), u16(0),
      u32(crc),
      u32(compressed.length),
      u32(data.length),
      u16(name.length), u16(0), u16(0),
      u16(0), u16(0),
      u32(0),
      u32(offset),
      name,
    ]);

    locals.push(local);
    centrals.push(central);
    offset += local.length;
  }

  const centralDir = Buffer.concat(centrals);
  const end = Buffer.concat([
    u32(0x06054b50),
    u16(0), u16(0),
    u16(files.length), u16(files.length),
    u32(centralDir.length),
    u32(offset),
    u16(0),
  ]);

  return Buffer.concat([...locals, centralDir, end]);
}

function buildBridgeZip() {
  const bridgePy = fs.readFileSync(path.join(__dirname, 'companion_bridge.py'));
  const runBat   = fs.readFileSync(path.join(__dirname, 'bridge', 'run_bridge.bat'));
  const readme   = fs.readFileSync(path.join(__dirname, 'bridge', 'README.txt'));
  return buildZip([
    { name: 'companion-bridge/companion_bridge.py', data: bridgePy },
    { name: 'companion-bridge/run_bridge.bat', data: runBat },
    { name: 'companion-bridge/README.txt', data: readme },
  ]);
}

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

function normalizeDashUrl(raw) {
  if (typeof raw !== 'string') return '';
  let url = raw.trim();
  if (!url) return '';
  // Users often paste host:port/path — make it absolute so the iframe
  // does not resolve against the Railway origin (Cannot GET /192.168...)
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(url)) {
    url = `http://${url}`;
  }
  return url;
}

function normalizeSecurity(sec) {
  const s = (sec && typeof sec === 'object') ? sec : {};
  const colors = Array.isArray(s.colors)
    ? s.colors.map(c => String(c).toLowerCase()).filter(Boolean)
    : ['red', 'green', 'blue'];
  return {
    enabled: s.enabled !== false, // on by default
    pin: String(s.pin ?? '1615').trim() || '1615',
    colors: colors.length ? colors : ['red', 'green', 'blue'],
  };
}

function normalizeState(state) {
  const buttons = Array.isArray(state?.buttons)
    ? state.buttons.map(normalizeButton)
    : [];
  const views = (state?.views && typeof state.views === 'object') ? state.views : {};
  const dashUrl = normalizeDashUrl(state?.dashUrl);
  const security = normalizeSecurity(state?.security);
  return { buttons, views, dashUrl, security };
}

function publicState() {
  return {
    buttons: appState.buttons,
    views: appState.views,
    dashUrl: appState.dashUrl,
    security: { enabled: !!appState.security?.enabled },
  };
}

// ── Trigger queue ─────────────────────────────────────────────────────────────
const triggerQueue = [];

// ── Companion Satellite feedback cache ────────────────────────────────────────
// Keyed by "page/row/col"
const buttonFeedback = new Map();
function locKey(page, row, col) {
  return `${page}/${row}/${col}`;
}

// Accept updates pushed by the local Python bridge.
app.post('/feedback', checkSecret, (req, res) => {
  const { page, row, col, pressed, color, text } = req.body || {};
  const p = Number(page);
  const r = Number(row);
  const c = Number(col);
  if (!Number.isFinite(p) || !Number.isFinite(r) || !Number.isFinite(c)) {
    return res.status(400).json({ error: 'Expected numeric page/row/col' });
  }
  const key = locKey(p, r, c);
  buttonFeedback.set(key, {
    pressed: !!pressed,
    color: typeof color === 'string' ? color : null,
    text: typeof text === 'string' ? text : null,
    updatedAt: Date.now(),
  });
  res.json({ ok: true });
});

// Let the browser poll the latest feedback for on-screen buttons.
app.get('/feedback', (req, res) => {
  const now = Date.now();
  const out = {};
  for (const [k, v] of buttonFeedback.entries()) {
    // prune entries older than ~15s
    if (now - v.updatedAt > 15000) continue;
    out[k] = { pressed: v.pressed, color: v.color, text: v.text };
  }
  res.json({ ok: true, feedback: out, updatedAt: now });
});

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
  security: {
    enabled: true,
    pin: '1615',
    colors: ['red', 'green', 'blue'],
  },
});

// Simple unlock rate limit (per IP)
const unlockAttempts = new Map();
function unlockRateLimited(ip) {
  const now = Date.now();
  const row = unlockAttempts.get(ip) || { n: 0, t: now };
  if (now - row.t > 60000) { row.n = 0; row.t = now; }
  row.n += 1;
  unlockAttempts.set(ip, row);
  return row.n > 12;
}
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
  res.json(publicState());
});

// Full security config — secret required (for Admin)
app.get('/security', checkSecret, (req, res) => {
  res.json(appState.security || normalizeSecurity({}));
});

app.put('/state', checkSecret, (req, res) => {
  const { buttons, views, dashUrl, security } = req.body;
  if (!Array.isArray(buttons) || typeof views !== 'object') {
    return res.status(400).json({ error: 'Expected { buttons: [], views: {}, dashUrl?: string, security?: {} }' });
  }
  // Keep existing security if client omitted it (avoid wiping on partial push)
  const next = { buttons, views, dashUrl };
  next.security = security !== undefined ? security : appState.security;
  appState = normalizeState(next);
  console.log(`[state] PUT — ${appState.buttons.length} buttons, ${Object.keys(appState.views).length} views, security=${appState.security.enabled ? 'on' : 'off'}`);
  res.json({ ok: true });
});

app.post('/unlock', (req, res) => {
  const ip = req.headers['x-forwarded-for']?.split(',')[0]?.trim() || req.socket.remoteAddress || 'unknown';
  if (unlockRateLimited(ip)) {
    return res.status(429).json({ error: 'Too many attempts — wait a minute' });
  }

  const sec = appState.security || normalizeSecurity({});
  if (!sec.enabled) {
    return res.json({ ok: true, unlocked: true, reason: 'disabled' });
  }

  const pin = String(req.body?.pin ?? '').trim();
  const colors = Array.isArray(req.body?.colors)
    ? req.body.colors.map(c => String(c).toLowerCase().trim())
    : [];

  const pinOk = pin === String(sec.pin);
  const colorsOk = colors.length === sec.colors.length
    && colors.every((c, i) => c === sec.colors[i]);

  if (!pinOk || !colorsOk) {
    console.log(`[unlock] FAIL from ${ip}`);
    return res.status(401).json({ error: 'Incorrect code' });
  }

  console.log(`[unlock] OK from ${ip}`);
  res.json({ ok: true, unlocked: true });
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
  // Prefer JSON idle response — empty 204 bodies break some HTTP clients' JSON parsers
  if (triggerQueue.length === 0) return res.json({ waiting: true });
  const item = triggerQueue.shift();
  console.log(`[poll] dispatching P${item.page}/R${item.row}/C${item.col}`);
  res.json({ page: item.page, row: item.row, col: item.col });
});

// Downloadable Windows bridge pack (Python + .bat)
app.get('/download/companion-bridge.zip', (req, res) => {
  try {
    const zip = buildBridgeZip();
    res.set({
      'Content-Type': 'application/zip',
      'Content-Disposition': 'attachment; filename="companion-bridge.zip"',
      'Content-Length': String(zip.length),
      'Cache-Control': 'no-store',
    });
    res.send(zip);
  } catch (e) {
    console.error('[download] bridge zip failed:', e);
    res.status(500).json({ error: 'Could not build bridge zip' });
  }
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

app.get('/unlock.js', (req, res) => {
  res.set('Cache-Control', 'no-store');
  res.type('application/javascript');
  res.sendFile(path.join(__dirname, 'unlock.js'));
});

// ── START ─────────────────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`Companion Bridge running on port ${PORT}`);
  console.log(`  GET  /            → surface.html`);
  console.log(`  GET  /dashboard   → Companion buttons + Ross DashBoard`);
  console.log(`  GET  /download/companion-bridge.zip → Windows bridge pack`);
  console.log(`  GET  /state       → button layout`);
  console.log(`  PUT  /state       → save layout (secret required)`);
  console.log(`  POST /trigger     → queue a button press (page/row/col)`);
  console.log(`  GET  /poll        → dequeue for OSC /location/.../press`);
});
