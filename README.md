# Companion Bridge — Full Stack

Compatible with **Bitfocus Companion 3.5.5** (location-based OSC).

```
[Browser — surface.html]
        │  POST /trigger { page, row, col }
        ▼
[Railway — server.js]   ←──  PUT /state  ──  [admin.html]
        │  GET /poll (Python polls this)
        ▼
[companion_bridge.py — runs on your local machine]
        │  UDP OSC  /location/{page}/{row}/{col}/press
        ▼
[Bitfocus Companion 3.x]
        │
        ▼
[Your gear]
```

---

## Files

| File                     | Purpose                                              |
|--------------------------|------------------------------------------------------|
| `server.js`              | Railway Express server — queue + state store         |
| `package.json`           | Node dependencies                                    |
| `surface.html`           | Public control surface — served at `/`               |
| `dashboard.html`         | Buttons on top + Ross DashBoard embed — `/dashboard` |
| `admin.html`             | Admin — configure buttons and views                  |
| `companion_bridge.py`    | Local Python GUI — polls Railway, fires OSC          |
| `requirements.txt`       | No pip installs needed (pure stdlib)                 |

---

## 1 — Deploy Railway server

Push `server.js` + `package.json` + `surface.html` + `admin.html` to GitHub,
then create a Railway project from the repo.

**Environment variables** (Railway → Variables tab):

| Variable     | Example                  | Notes                              |
|--------------|--------------------------|------------------------------------|
| API_SECRET   | some-long-random-string  | Used by admin + Python app         |

---

## 2 — Configure Companion OSC

In Bitfocus Companion → Settings → OSC Listener / OSC:

- Enable the OSC listener
- Port: **12321** (default, configurable in the Python app)
- You do **not** need Legacy OSC API enabled

OSC address used by this project:

```
/location/{page}/{row}/{column}/press
```

Example: page 1, row 0, column 5 → `/location/1/0/5/press`

Row and column are **0-based** (Companion 3.x grid coordinates).

---

## 3 — Run the Python bridge

```bash
python3 companion_bridge.py
```

Fill in the GUI:
- **Railway URL** — your Railway app URL
- **API Secret** — same value as Railway `API_SECRET` env var
- **Companion Host** — IP of the machine running Companion (or `127.0.0.1`)
- **Companion OSC Port** — `12321` (Companion default)
- Click **▶ START**

The app polls Railway every second, fires OSC to Companion for each trigger,
and shows a live activity log. Settings are saved to `bridge_config.json`
next to the script and restored on next launch.

Click **TEST OSC** to send a test `/location/1/0/0/press` to verify Companion connectivity
before going live.

---

## How it works

1. User opens `https://your-app.railway.app/` — no login
2. User clicks a button → `POST /trigger { page, row, col }` to Railway
3. Railway enqueues the trigger (in-memory queue, 10s TTL)
4. Python app polls `GET /poll` — dequeues and sends UDP OSC to Companion
5. Companion fires the button at that page/row/column

Triggers older than 10 seconds are automatically dropped
(so a backlog doesn't fire if the Python app goes offline briefly).

---

## Notes

- The Python app requires no pip installs — it uses only Python stdlib.
- State (buttons/views) is stored in Railway's process memory.
  Add a Railway Postgres addon and persist `appState` to a JSON column
  for durability across redeploys.
- `surface.html` and `admin.html` can also be hosted on Netlify/Cloudflare Pages
  as static files — just point them at your Railway URL.
- If an old config still has Companion 2.x `bank` values, the server converts
  them to `row`/`col` on an 8-wide grid when loading/saving.
