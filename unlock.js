/**
 * Venue unlock gate — PIN + color sequence (default: 1615 then Red→Green→Blue).
 * Usage:
 *   CBUnlock.require({ serverUrl, onUnlocked })
 */
(function (global) {
  const SESSION_KEY = 'cb_unlock_ok';
  const SESSION_TTL_MS = 8 * 60 * 60 * 1000; // 8 hours

  const COLOR_DEFS = [
    { id: 'red',    hex: '#e63946', label: 'R' },
    { id: 'orange', hex: '#ff6b35', label: 'O' },
    { id: 'yellow', hex: '#e9c46a', label: 'Y' },
    { id: 'green',  hex: '#00e676', label: 'G' },
    { id: 'cyan',   hex: '#00d4ff', label: 'C' },
    { id: 'blue',   hex: '#3a86ff', label: 'B' },
    { id: 'purple', hex: '#a855f7', label: 'P' },
    { id: 'pink',   hex: '#ec4899', label: 'K' },
    { id: 'white',  hex: '#e8eaed', label: 'W' },
    { id: 'gray',   hex: '#6b7280', label: 'N' },
    { id: 'teal',   hex: '#14b8a6', label: 'T' },
    { id: 'lime',   hex: '#a3e635', label: 'L' },
  ];

  function isUnlocked() {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (!raw) return false;
      const data = JSON.parse(raw);
      if (!data?.ok || !data?.ts) return false;
      if (Date.now() - data.ts > SESSION_TTL_MS) {
        sessionStorage.removeItem(SESSION_KEY);
        return false;
      }
      return true;
    } catch {
      return false;
    }
  }

  function markUnlocked() {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ ok: true, ts: Date.now() }));
  }

  function clearUnlock() {
    sessionStorage.removeItem(SESSION_KEY);
  }

  function injectStyles() {
    if (document.getElementById('cb-unlock-styles')) return;
    const style = document.createElement('style');
    style.id = 'cb-unlock-styles';
    style.textContent = `
      #cb-unlock {
        position: fixed; inset: 0; z-index: 20000;
        background: #080a0c;
        display: flex; align-items: center; justify-content: center;
        font-family: 'Barlow Condensed', sans-serif;
      }
      #cb-unlock.hidden { display: none; }
      .cb-unlock-box {
        width: min(420px, 94vw);
        background: #0f1115; border: 1px solid #252a35;
        padding: 28px 24px 24px;
        display: flex; flex-direction: column; gap: 18px;
      }
      .cb-unlock-title {
        font-family: 'Share Tech Mono', monospace;
        font-size: 11px; letter-spacing: 0.2em; color: #00d4ff;
      }
      .cb-unlock-desc {
        font-family: 'Share Tech Mono', monospace;
        font-size: 10px; color: #4e5668; line-height: 1.6; letter-spacing: 0.04em;
      }
      .cb-unlock-step {
        font-family: 'Share Tech Mono', monospace;
        font-size: 10px; letter-spacing: 0.16em; color: #7c8499;
        text-transform: uppercase;
      }
      .cb-unlock-step strong { color: #c8cdd8; }
      .cb-pin-display {
        height: 48px; background: #161920; border: 1px solid #252a35;
        display: flex; align-items: center; justify-content: center; gap: 10px;
        font-family: 'Share Tech Mono', monospace; font-size: 22px;
        letter-spacing: 0.35em; color: #c8cdd8;
      }
      .cb-pin-pad {
        display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px;
      }
      .cb-pin-key, .cb-color-cell {
        height: 52px; border: 1px solid #252a35; background: #161920;
        color: #c8cdd8; font-family: 'Barlow Condensed', sans-serif;
        font-size: 20px; font-weight: 700; cursor: pointer;
        -webkit-tap-highlight-color: transparent; user-select: none;
      }
      .cb-pin-key:hover { border-color: #00d4ff; color: #00d4ff; }
      .cb-pin-key:active { transform: scale(0.96); }
      .cb-color-grid {
        display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
      }
      .cb-color-cell {
        height: 56px; position: relative; overflow: hidden;
      }
      .cb-color-cell::before {
        content: ''; position: absolute; inset: 0;
        background: var(--c); opacity: 0.95;
      }
      .cb-color-cell span {
        position: relative; z-index: 1;
        font-family: 'Share Tech Mono', monospace; font-size: 11px;
        color: #000; mix-blend-mode: soft-light; font-weight: 700;
      }
      .cb-color-cell.picked { outline: 2px solid #fff; outline-offset: 1px; }
      .cb-color-trail {
        min-height: 28px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap;
      }
      .cb-color-dot {
        width: 18px; height: 18px; border: 1px solid #252a35;
        background: var(--c, #161920);
      }
      .cb-unlock-err {
        font-family: 'Share Tech Mono', monospace; font-size: 11px;
        color: #ff3b3b; min-height: 16px; letter-spacing: 0.06em;
      }
      .cb-unlock-actions { display: flex; gap: 8px; }
      .cb-unlock-btn {
        flex: 1; height: 44px; border: none; cursor: pointer;
        font-family: 'Barlow Condensed', sans-serif; font-size: 15px;
        font-weight: 900; letter-spacing: 0.14em; text-transform: uppercase;
      }
      .cb-unlock-btn.primary { background: #00d4ff; color: #000; }
      .cb-unlock-btn.ghost { background: transparent; border: 1px solid #252a35; color: #7c8499; }
      .cb-unlock-btn:disabled { opacity: 0.4; cursor: not-allowed; }
    `;
    document.head.appendChild(style);
  }

  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  async function requireUnlock(opts) {
    const serverUrl = (opts.serverUrl || '').replace(/\/$/, '');
    const onUnlocked = opts.onUnlocked || (() => {});

    if (isUnlocked()) {
      onUnlocked();
      return true;
    }

    // Ask server if security is enabled
    let enabled = true;
    try {
      if (serverUrl) {
        const r = await fetch(serverUrl + '/state');
        if (r.ok) {
          const data = await r.json();
          enabled = data?.security?.enabled !== false;
        }
      }
    } catch (_) { /* treat as enabled */ }

    if (!enabled) {
      markUnlocked();
      onUnlocked();
      return true;
    }

    injectStyles();
    return showGate(serverUrl, onUnlocked);
  }

  function showGate(serverUrl, onUnlocked) {
    return new Promise((resolve) => {
      let existing = document.getElementById('cb-unlock');
      if (existing) existing.remove();

      let pin = '';
      let picked = [];
      let phase = 'pin'; // pin | colors

      const el = document.createElement('div');
      el.id = 'cb-unlock';
      el.innerHTML = `
        <div class="cb-unlock-box">
          <div class="cb-unlock-title">// ACCESS LOCK</div>
          <div class="cb-unlock-desc">Enter the venue code, then select the color sequence.</div>
          <div class="cb-unlock-step" id="cb-unlock-step"><strong>STEP 1</strong> — PIN</div>
          <div id="cb-pin-panel">
            <div class="cb-pin-display" id="cb-pin-display">••••</div>
            <div class="cb-pin-pad" id="cb-pin-pad"></div>
          </div>
          <div id="cb-color-panel" style="display:none">
            <div class="cb-color-trail" id="cb-color-trail"></div>
            <div class="cb-color-grid" id="cb-color-grid"></div>
          </div>
          <div class="cb-unlock-err" id="cb-unlock-err"></div>
          <div class="cb-unlock-actions">
            <button class="cb-unlock-btn ghost" type="button" id="cb-unlock-clear">CLEAR</button>
            <button class="cb-unlock-btn primary" type="button" id="cb-unlock-go">CONTINUE</button>
          </div>
        </div>
      `;
      document.body.appendChild(el);

      const pinPad = el.querySelector('#cb-pin-pad');
      ['1','2','3','4','5','6','7','8','9','CLR','0','⌫'].forEach((k) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'cb-pin-key';
        b.textContent = k;
        b.addEventListener('click', () => {
          if (k === 'CLR') pin = '';
          else if (k === '⌫') pin = pin.slice(0, -1);
          else if (pin.length < 8) pin += k;
          renderPin();
        });
        pinPad.appendChild(b);
      });

      const grid = el.querySelector('#cb-color-grid');
      shuffle(COLOR_DEFS).forEach((c) => {
        const cell = document.createElement('button');
        cell.type = 'button';
        cell.className = 'cb-color-cell';
        cell.style.setProperty('--c', c.hex);
        cell.innerHTML = `<span>${c.label}</span>`;
        cell.addEventListener('click', () => {
          if (phase !== 'colors') return;
          picked.push(c.id);
          cell.classList.add('picked');
          setTimeout(() => cell.classList.remove('picked'), 180);
          renderTrail();
          if (picked.length >= 3) tryUnlock();
        });
        grid.appendChild(cell);
      });

      function renderPin() {
        const d = el.querySelector('#cb-pin-display');
        d.textContent = pin ? '•'.repeat(pin.length) : '••••';
      }

      function renderTrail() {
        const trail = el.querySelector('#cb-color-trail');
        trail.innerHTML = '';
        picked.forEach((id) => {
          const def = COLOR_DEFS.find(c => c.id === id);
          const dot = document.createElement('div');
          dot.className = 'cb-color-dot';
          if (def) dot.style.setProperty('--c', def.hex);
          trail.appendChild(dot);
        });
      }

      function setErr(msg) {
        el.querySelector('#cb-unlock-err').textContent = msg || '';
      }

      function goColors() {
        if (pin.length < 4) {
          setErr('Enter the 4-digit code');
          return;
        }
        phase = 'colors';
        picked = [];
        el.querySelector('#cb-pin-panel').style.display = 'none';
        el.querySelector('#cb-color-panel').style.display = 'block';
        el.querySelector('#cb-unlock-step').innerHTML = '<strong>STEP 2</strong> — COLOR SEQUENCE';
        el.querySelector('#cb-unlock-go').textContent = 'UNLOCK';
        setErr('');
        renderTrail();
      }

      async function tryUnlock() {
        setErr('');
        const goBtn = el.querySelector('#cb-unlock-go');
        goBtn.disabled = true;
        try {
          if (!serverUrl) {
            // Offline fallback — hard defaults
            if (pin === '1615' && picked.join(',') === 'red,green,blue') {
              markUnlocked();
              el.classList.add('hidden');
              onUnlocked();
              resolve(true);
              return;
            }
            setErr('Incorrect code');
            picked = [];
            renderTrail();
            return;
          }
          const r = await fetch(serverUrl + '/unlock', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pin, colors: picked }),
          });
          const data = await r.json().catch(() => ({}));
          if (!r.ok || !data.ok) {
            setErr(data.error || 'Incorrect code');
            picked = [];
            renderTrail();
            return;
          }
          markUnlocked();
          el.classList.add('hidden');
          onUnlocked();
          resolve(true);
        } catch (e) {
          setErr('Cannot reach server');
        } finally {
          goBtn.disabled = false;
        }
      }

      el.querySelector('#cb-unlock-clear').addEventListener('click', () => {
        if (phase === 'pin') {
          pin = '';
          renderPin();
        } else {
          picked = [];
          renderTrail();
        }
        setErr('');
      });

      el.querySelector('#cb-unlock-go').addEventListener('click', () => {
        if (phase === 'pin') goColors();
        else tryUnlock();
      });

      renderPin();
    });
  }

  global.CBUnlock = {
    require: requireUnlock,
    isUnlocked,
    clear: clearUnlock,
  };
})(window);
