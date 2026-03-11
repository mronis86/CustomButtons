# Companion Bridge — Full Stack

```
[Browser — surface.html]
        │  POST /trigger
        ▼
[Railway — server.js]   ←──  PUT /state  ──  [admin.html]
        │  GET /poll (Python polls this)
        ▼
[companion_bridge.py — runs on your local machine]
        │  UDP OSC  /press/bank/{page}/{bank}
        ▼
[Bitfocus Companion]
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

In Bitfocus Companion → Settings → OSC/UDP API:
- Enable the OSC server
- Port: **12321** (default, configurable in the Python app)
- The OSC address used is `/press/bank/{page}/{bank}`

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

Click **TEST OSC** to send a test `/press/bank/1/1` to verify Companion connectivity
before going live.

---

## How it works

1. User opens `https://your-app.railway.app/` — no login
2. User clicks a button → `POST /trigger {page, bank}` to Railway
3. Railway enqueues the trigger (in-memory queue, 10s TTL)
4. Python app polls `GET /poll` — dequeues and sends UDP OSC to Companion
5. Companion fires the button

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
