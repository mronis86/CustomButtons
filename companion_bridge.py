#!/usr/bin/env python3
"""
Companion Bridge — Local OSC relay
Polls a Railway server for pending trigger commands,
fires OSC /location/{page}/{row}/{col}/press to Bitfocus Companion 3.x+.

Compatible with Companion 3.5.5 location-based OSC API.
Legacy /press/bank/... is intentionally not used.

Requirements: pure Python stdlib (no pip installs needed).
"""

import json
import base64
import os
import queue
import socket
import select
import struct
import shlex
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import scrolledtext, ttk
import urllib.request
import urllib.error

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "bridge_config.json")

# ─── OSC implementation (no dependency on python-osc if not installed) ─────────
# We include a minimal OSC builder so the app works with zero pip installs.
# If python-osc IS installed it will be used instead for full compatibility.

def _osc_string(s):
    s = s.encode("utf-8") + b"\x00"
    pad = (4 - len(s) % 4) % 4
    return s + b"\x00" * pad

def _osc_build(address, *args):
    """Build a minimal OSC message with int or float args."""
    msg = _osc_string(address)
    type_tag = "," + "".join("i" if isinstance(a, int) else "f" for a in args)
    msg += _osc_string(type_tag)
    for a in args:
        if isinstance(a, int):
            msg += struct.pack(">i", a)
        else:
            msg += struct.pack(">f", a)
    return msg

def normalize_companion_host(host):
    """Strip URL junk so users can paste IPs or accidental http:// URLs."""
    host = (host or "").strip()
    if not host:
        return "127.0.0.1"
    # http://192.168.1.10:8000/path → 192.168.1.10
    for prefix in ("http://", "https://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
            break
    host = host.split("/")[0].strip()
    # drop accidental :port in host field (port has its own field)
    if host.count(":") == 1 and not host.startswith("["):
        maybe_ip, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = maybe_ip
    host = host.strip("[]")  # IPv6 brackets if someone used them alone
    return host or "127.0.0.1"

def send_osc(host, port, address, *args):
    host = normalize_companion_host(host)
    port = int(port)
    data = _osc_build(address, *args)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(data, (host, port))
    finally:
        sock.close()
    return host, port

# ─── Config ───────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "railway_url":    "",
    "api_secret":     "",
    "companion_host": "127.0.0.1",
    "companion_port": "12321",
    "companion_satellite_port": "16622",
    "poll_interval":  "1.0",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            # fill missing keys with defaults
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ─── Poller thread ────────────────────────────────────────────────────────────

class Poller(threading.Thread):
    """
    Polls GET /poll from the Railway server.
    The server returns the next queued trigger as JSON { page, row, col }
    or { waiting: true } / empty / 204 if nothing is pending.
    """
    def __init__(self, cfg, log_queue, stop_event):
        super().__init__(daemon=True)
        self.cfg         = cfg
        self.log_queue   = log_queue
        self.stop_event  = stop_event

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] [{level}] {msg}")

    def run(self):
        self.log("Poller started (Companion 3.x /location OSC)")
        while not self.stop_event.is_set():
            try:
                self._poll_once()
            except Exception as e:
                self.log(f"Poll error: {e}", "ERR")
                time.sleep(3)
            interval = float(self.cfg.get("poll_interval", 1.0))
            time.sleep(interval)
        self.log("Poller stopped")

    def _poll_once(self):
        url    = self.cfg["railway_url"].rstrip("/") + "/poll"
        secret = self.cfg.get("api_secret", "")

        req = urllib.request.Request(url)
        if secret:
            req.add_header("x-api-secret", secret)

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                # 204 No Content = queue empty (urllib does NOT raise for 204)
                if getattr(resp, "status", None) == 204:
                    return
                raw = resp.read().decode("utf-8", errors="replace").strip()
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return  # nothing waiting
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            self.log(f"Poll HTTP {e.code}: {body or e.reason}", "ERR")
            time.sleep(2)
            return
        except urllib.error.URLError as e:
            self.log(f"Cannot reach Railway: {e.reason}", "ERR")
            time.sleep(5)
            return

        # Empty body also means nothing waiting
        if not raw:
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self.log(f"Poll non-JSON response: {raw[:160]!r}", "ERR")
            time.sleep(2)
            return

        if data.get("waiting"):
            return

        # row/col may be 0 — do not use truthiness checks
        if "page" not in data or "row" not in data or "col" not in data:
            self.log(f"Bad trigger payload (need page/row/col): {data}", "WARN")
            return

        try:
            page = int(data["page"])
            row  = int(data["row"])
            col  = int(data["col"])
        except (TypeError, ValueError):
            self.log(f"Non-numeric page/row/col: {data}", "WARN")
            return

        self._fire_osc(page, row, col)

    def _fire_osc(self, page, row, col):
        host    = self.cfg["companion_host"]
        port    = self.cfg["companion_port"]
        # Companion 3.x / 4.x: /location/{page}/{row}/{column}/press
        address = f"/location/{page}/{row}/{col}/press"
        try:
            host, port = send_osc(host, port, address)
            self.log(f"OSC  {address}  →  {host}:{port}", "OSC")
        except OSError as e:
            self.log(
                f"OSC failed ({e}) — check Companion Host is an IP like 127.0.0.1 or 192.168.x.x (not a URL)",
                "ERR",
            )
        except Exception as e:
            self.log(f"OSC failed: {e}", "ERR")

# ─── Satellite feedback thread ─────────────────────────────────────────────

def _parse_port(raw, default=16622):
    s = str(raw or "").strip()
    digits = "".join(ch for ch in s if ch.isdigit())
    try:
        return int(digits) if digits else default
    except ValueError:
        return default

def _parse_kv_line(line):
    """Parse Satellite protocol line into (cmd, {key: value})."""
    try:
        parts = shlex.split(line, posix=True)
    except Exception:
        parts = line.split()
    if not parts:
        return None, {}
    cmd = parts[0]
    kv = {}
    for tok in parts[1:]:
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        kv[k] = v
    return cmd, kv

def _decode_b64_text(raw):
    if raw is None:
        return None
    try:
        return base64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return raw

class SatelliteFeedback(threading.Thread):
    """
    Pull Companion button style/pressed state via Satellite API and POST to Railway.

    Companion 4.3+ (Satellite API 1.10+): ADD-SUB per page/row/col (preferred).
    Companion 4.2.x: ADD-DEVICE surface fallback — maps KEY row/col onto each
    page that appears in the button layout (one virtual surface per page).
    """

    def __init__(self, cfg, log_queue, stop_event):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.log_queue = log_queue
        self.stop_event = stop_event
        self.sock = None
        self.api_version = (0, 0, 0)
        self.subscriptions_ok = False
        self.subid_to_loc = {}
        self.current_subids = set()
        self.device_pages = set()  # pages we registered as surfaces
        self.last_sent = {}
        self.pending = []  # batch POSTs
        self.last_flush = 0.0

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] [{level}] {msg}")

    def _rail(self, path):
        return self.cfg["railway_url"].rstrip("/") + path

    def _fetch_locations(self):
        try:
            req = urllib.request.Request(self._rail("/state"))
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            locs = set()
            for b in (data.get("buttons") or []):
                try:
                    locs.add((int(b["page"]), int(b.get("row") or 0), int(b.get("col") or 0)))
                except Exception:
                    continue
            return locs
        except Exception as e:
            self.log(f"Satellite: cannot fetch /state: {e}", "WARN")
            return set()

    def _send_line(self, line):
        if not self.sock:
            return
        if isinstance(line, str):
            line = line.encode("utf-8")
        self.sock.sendall(line + b"\n")

    def _queue_feedback(self, page, row, col, pressed=None, color=None, text=None):
        self.pending.append({
            "page": page,
            "row": row,
            "col": col,
            "pressed": pressed,
            "color": color,
            "text": text,
        })

    def _flush_feedback(self, force=False):
        now = time.time()
        if not self.pending:
            return
        if not force and (now - self.last_flush) < 0.25:
            return
        updates = self.pending
        self.pending = []
        self.last_flush = now

        # Dedupe by loc, keep last
        merged = {}
        for u in updates:
            key = f"{u['page']}/{u['row']}/{u['col']}"
            prev = merged.get(key, {})
            merged[key] = {
                "page": u["page"],
                "row": u["row"],
                "col": u["col"],
                "pressed": u["pressed"] if u["pressed"] is not None else prev.get("pressed"),
                "color": u["color"] if u["color"] else prev.get("color"),
                "text": u["text"] if u["text"] else prev.get("text"),
            }

        payload = {"updates": list(merged.values())}
        secret = self.cfg.get("api_secret", "")
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["x-api-secret"] = secret
        try:
            req = urllib.request.Request(
                self._rail("/feedback"), data=data, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
            self.log(f"Satellite: pushed {len(merged)} feedback update(s)", "INFO")
        except Exception as e:
            self.log(f"Satellite: POST /feedback failed: {e}", "WARN")

    def _parse_api_version(self, raw):
        try:
            parts = [int(x) for x in str(raw).split(".")[:3]]
            while len(parts) < 3:
                parts.append(0)
            return tuple(parts)
        except Exception:
            return (0, 0, 0)

    def _handle_line(self, line):
        cmd, kv = _parse_kv_line(line)
        if not cmd:
            return

        if cmd == "BEGIN":
            self.api_version = self._parse_api_version(kv.get("ApiVersion", "0.0.0"))
            self.log(
                f"Satellite BEGIN ApiVersion={kv.get('ApiVersion')} Companion={kv.get('CompanionVersion')}",
                "INFO",
            )
            return

        if cmd == "CAPS":
            self.subscriptions_ok = str(kv.get("SUBSCRIPTIONS", "0")) in ("1", "true")
            self.log(f"Satellite CAPS subscriptions={self.subscriptions_ok}", "INFO")
            return

        if cmd == "PING":
            payload = " ".join(f"{k}={v}" for k, v in kv.items()) if kv else ""
            # PING may also be: PING 12345
            if not payload and "=" not in line:
                parts = line.split(None, 1)
                payload = parts[1] if len(parts) > 1 else ""
            try:
                self._send_line(f"PONG {payload}".strip())
            except Exception:
                pass
            return

        if cmd.endswith("ERROR") or (len(line.split()) >= 2 and line.split()[1] == "ERROR"):
            self.log(f"Satellite: {line}", "ERR")
            return

        if cmd == "ERROR":
            self.log(f"Satellite: {line}", "ERR")
            return

        if cmd == "SUB-STATE":
            subid = kv.get("SUBID")
            loc = self.subid_to_loc.get(subid)
            if not loc:
                return
            page, row, col = loc
            pressed = str(kv.get("PRESSED", "0")).lower() in ("1", "true")
            color = kv.get("COLOR")
            text = _decode_b64_text(kv.get("TEXT"))
            self._queue_feedback(page, row, col, pressed=pressed, color=color, text=text)
            return

        if cmd == "KEY-STATE":
            # Surface fallback (Companion < 4.3 / no subscriptions)
            device = kv.get("DEVICEID", "")
            page = None
            if device.startswith("cbpage_"):
                try:
                    page = int(device.split("_", 1)[1])
                except Exception:
                    page = None

            row = col = None
            loc = kv.get("LOCATION")  # page/row/col when API >= 1.10
            if loc and loc.count("/") == 2:
                try:
                    p, r, c = loc.split("/")
                    page = int(p)
                    row = int(r)
                    col = int(c)
                except Exception:
                    pass

            key = kv.get("KEY") or kv.get("CONTROLID")
            if row is None and key:
                # KEY may be "0/0" or an index
                if "/" in str(key):
                    try:
                        row, col = [int(x) for x in str(key).split("/")[:2]]
                    except Exception:
                        pass
                else:
                    try:
                        idx = int(key)
                        cols = 8
                        row, col = divmod(idx, cols)
                    except Exception:
                        pass

            if page is None or row is None or col is None:
                return

            pressed = str(kv.get("PRESSED", "0")).lower() in ("1", "true")
            color = kv.get("COLOR")
            text = _decode_b64_text(kv.get("TEXT"))
            self._queue_feedback(page, row, col, pressed=pressed, color=color, text=text)
            return

        # Ignore OK / unknown chatter

    def _sync_subscriptions(self, locs):
        desired = set()
        for page, row, col in locs:
            subid = f"sub_{page}_{row}_{col}"
            desired.add(subid)
            self.subid_to_loc[subid] = (page, row, col)

        for subid in desired - self.current_subids:
            page, row, col = self.subid_to_loc[subid]
            self._send_line(
                f"ADD-SUB SUBID={subid} LOCATION={page}/{row}/{col} "
                f"BITMAP=0 COLORS=hex TEXT=true"
            )
            self.log(f"Satellite: ADD-SUB {page}/{row}/{col}", "INFO")

        for subid in self.current_subids - desired:
            self._send_line(f"REMOVE-SUB SUBID={subid}")
            self.subid_to_loc.pop(subid, None)

        self.current_subids = desired

    def _sync_surfaces(self, locs):
        """Companion 4.2 fallback: one virtual surface per page."""
        pages = sorted({p for p, _, _ in locs})
        # Determine grid size from locs
        max_row = max((r for _, r, _ in locs), default=3)
        max_col = max((c for _, _, c in locs), default=7)
        keys_per_row = max(max_col + 1, 8)
        keys_total = keys_per_row * max(max_row + 1, 4)

        for page in pages:
            if page in self.device_pages:
                continue
            device_id = f"cbpage_{page}"
            # Simple surface — user must set this surface's page in Companion UI once
            self._send_line(
                f'ADD-DEVICE DEVICEID={device_id} PRODUCT_NAME="CB Feedback P{page}" '
                f"KEYS_TOTAL={keys_total} KEYS_PER_ROW={keys_per_row} "
                f"BITMAPS=0 COLORS=hex TEXT=true"
            )
            self.device_pages.add(page)
            self.log(
                f"Satellite: ADD-DEVICE {device_id} — in Companion Surfaces, "
                f"set this device to page {page}",
                "WARN",
            )

    def run(self):
        self.log("Satellite feedback started", "INFO")
        while not self.stop_event.is_set():
            host = normalize_companion_host(self.cfg.get("companion_host", "127.0.0.1"))
            port = _parse_port(self.cfg.get("companion_satellite_port"), 16622)

            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(5)
                self.sock.connect((host, port))
                self.sock.settimeout(None)
                self.log(f"Satellite connected {host}:{port}", "OK")

                buffer = b""
                last_ping = 0.0
                last_sync = 0.0
                self.api_version = (0, 0, 0)
                self.subscriptions_ok = False
                self.subid_to_loc.clear()
                self.current_subids.clear()
                self.device_pages.clear()
                self.pending = []
                greeted = False

                while not self.stop_event.is_set():
                    now = time.time()

                    if now - last_ping > 2.0:
                        try:
                            self._send_line(f"PING {int(now)}")
                        except Exception:
                            pass
                        last_ping = now

                    self._flush_feedback()

                    if greeted and (now - last_sync > 5.0):
                        locs = self._fetch_locations()
                        if locs:
                            # Prefer ADD-SUB when API supports it (Companion 4.3+)
                            use_subs = self.subscriptions_ok or self.api_version >= (1, 10, 0)
                            if use_subs:
                                self._sync_subscriptions(locs)
                            else:
                                if self.api_version and self.api_version < (1, 10, 0):
                                    self.log(
                                        "Satellite API < 1.10 (Companion 4.2.x): "
                                        "using surface fallback — assign each CB Feedback surface to its page in Companion",
                                        "WARN",
                                    )
                                self._sync_surfaces(locs)
                        last_sync = now

                    r, _, _ = select.select([self.sock], [], [], 0.25)
                    if not r:
                        continue

                    chunk = self.sock.recv(8192)
                    if not chunk:
                        raise ConnectionError("Satellite socket closed")
                    buffer += chunk

                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        line = raw.strip().decode("utf-8", errors="replace")
                        if not line:
                            continue
                        if line.startswith("BEGIN"):
                            greeted = True
                        self._handle_line(line)

                self._flush_feedback(force=True)

            except Exception as e:
                self.log(f"Satellite feedback error: {e}", "WARN")
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None
                time.sleep(4)

# ─── GUI ──────────────────────────────────────────────────────────────────────

BG        = "#0d0f12"
SURFACE   = "#141720"
SURFACE2  = "#1c2030"
BORDER    = "#2a2e3a"
TEXT      = "#c8cdd8"
MUTED     = "#4e5668"
ACCENT    = "#00d4ff"
SUCCESS   = "#00e676"
DANGER    = "#ff3b3b"
WARN      = "#ffb700"

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Companion Bridge")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(560, 480)
        self.geometry("640x560")

        self.cfg         = load_config()
        self.log_queue   = queue.Queue()
        self.stop_event  = threading.Event()
        self.poller      = None
        self.satellite   = None
        self.running     = False

        self._build_ui()
        self._poll_log()

        # Auto-start if config looks complete
        if self.cfg.get("railway_url") and self.cfg.get("companion_host"):
            self.after(600, self.start_polling)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        mono  = tkfont.Font(family="Courier", size=10)
        label_font = tkfont.Font(family="Courier", size=9)

        # ── Header bar ──
        hdr = tk.Frame(self, bg=SURFACE, height=44)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        tk.Label(hdr, text="COMPANION BRIDGE", bg=SURFACE, fg=ACCENT,
                 font=("Courier", 12, "bold"), padx=16).pack(side="left", pady=10)

        self.status_canvas = tk.Canvas(hdr, width=12, height=12, bg=SURFACE,
                                       highlightthickness=0)
        self.status_canvas.pack(side="right", padx=(0, 8), pady=16)
        self.status_dot = self.status_canvas.create_oval(1,1,11,11, fill=MUTED, outline="")

        self.status_label = tk.Label(hdr, text="STOPPED", bg=SURFACE,
                                     fg=MUTED, font=("Courier", 9))
        self.status_label.pack(side="right", padx=(0, 4))

        sep = tk.Frame(self, bg=BORDER, height=1)
        sep.pack(fill="x")

        # ── Config section ──
        cfg_outer = tk.Frame(self, bg=BG)
        cfg_outer.pack(fill="x", padx=16, pady=(14, 0))

        tk.Label(cfg_outer, text="// CONFIGURATION", bg=BG, fg=MUTED,
                 font=("Courier", 9)).pack(anchor="w", pady=(0, 8))

        cfg_frame = tk.Frame(cfg_outer, bg=SURFACE, bd=0,
                             highlightthickness=1, highlightbackground=BORDER)
        cfg_frame.pack(fill="x")

        self.fields = {}
        rows = [
            ("Railway URL",      "railway_url",    "https://your-app.up.railway.app", False),
            ("API Secret",       "api_secret",      "optional",                        True),
            ("Companion Host",   "companion_host",  "192.168.1.x",                     False),
            ("Companion OSC Port","companion_port", "12321",                            False),
            ("Companion Satellite Port", "companion_satellite_port", "16622", False),
            ("Poll Interval (s)","poll_interval",   "1.0",                             False),
        ]

        for i, (lbl, key, placeholder, is_secret) in enumerate(rows):
            row = tk.Frame(cfg_frame, bg=SURFACE)
            row.pack(fill="x", padx=1, pady=1)

            tk.Label(row, text=lbl, bg=SURFACE, fg=MUTED,
                     font=("Courier", 9), width=20, anchor="w").pack(side="left", padx=(10,0), pady=6)

            entry = tk.Entry(row, bg=SURFACE2, fg=TEXT, insertbackground=ACCENT,
                             relief="flat", font=("Courier", 10),
                             highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=ACCENT,
                             show="•" if is_secret else "")
            entry.insert(0, self.cfg.get(key, ""))
            entry.pack(side="left", fill="x", expand=True, padx=(8, 10), pady=6, ipady=4)
            entry.bind("<FocusOut>", lambda e, k=key, ent=entry: self._field_changed(k, ent))
            entry.bind("<Return>",   lambda e, k=key, ent=entry: self._field_changed(k, ent))

            self.fields[key] = entry

            if i < len(rows) - 1:
                tk.Frame(cfg_frame, bg=BORDER, height=1).pack(fill="x", padx=1)

        # ── Buttons row ──
        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(fill="x", padx=16, pady=12)

        self.start_btn = self._mk_btn(btn_row, "▶  START", self.start_polling, ACCENT, "#000")
        self.start_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = self._mk_btn(btn_row, "■  STOP", self.stop_polling, SURFACE2, TEXT)
        self.stop_btn.pack(side="left", padx=(0, 8))
        self.stop_btn.config(state="disabled")

        self._mk_btn(btn_row, "TEST OSC", self.test_osc, SURFACE2, WARN).pack(side="left")

        self._mk_btn(btn_row, "CLEAR LOG", self.clear_log, SURFACE2, MUTED).pack(side="right")

        # ── Log section ──
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        log_hdr = tk.Frame(self, bg=SURFACE)
        log_hdr.pack(fill="x")
        tk.Label(log_hdr, text="// ACTIVITY LOG", bg=SURFACE, fg=MUTED,
                 font=("Courier", 9), padx=16).pack(side="left", pady=6)

        self.log_box = scrolledtext.ScrolledText(
            self, bg=BG, fg=TEXT, font=("Courier", 9),
            relief="flat", bd=0, padx=10, pady=8,
            insertbackground=ACCENT, state="disabled",
            wrap="word"
        )
        self.log_box.pack(fill="both", expand=True)

        # colour tags
        self.log_box.tag_config("OSC",  foreground=ACCENT)
        self.log_box.tag_config("ERR",  foreground=DANGER)
        self.log_box.tag_config("OK",   foreground=SUCCESS)
        self.log_box.tag_config("WARN", foreground=WARN)
        self.log_box.tag_config("INFO", foreground=MUTED)

    def _mk_btn(self, parent, text, cmd, bg, fg):
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=SURFACE2, activeforeground=ACCENT,
                         font=("Courier", 10, "bold"), relief="flat",
                         padx=14, pady=6, cursor="hand2",
                         highlightthickness=1, highlightbackground=BORDER)

    # ── Field changes ─────────────────────────────────────────────────────────

    def _field_changed(self, key, entry):
        val = entry.get().strip()
        if key == "companion_satellite_port":
            val = str(_parse_port(val, 16622))
            entry.delete(0, "end")
            entry.insert(0, val)
        if key == "companion_host":
            val = normalize_companion_host(val)
            entry.delete(0, "end")
            entry.insert(0, val)
        self.cfg[key] = val
        save_config(self.cfg)

    # ── Controls ─────────────────────────────────────────────────────────────

    def start_polling(self):
        # Save all fields first
        for key, entry in self.fields.items():
            self.cfg[key] = entry.get().strip()
        save_config(self.cfg)

        if not self.cfg.get("railway_url"):
            self._log("[ERR] Railway URL is required", "ERR")
            return

        self.stop_event.clear()
        self.poller = Poller(self.cfg, self.log_queue, self.stop_event)
        self.poller.start()
        self.satellite = SatelliteFeedback(self.cfg, self.log_queue, self.stop_event)
        self.satellite.start()
        self.running = True

        self._set_status("RUNNING", SUCCESS)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._log("Bridge started", "OK")

    def stop_polling(self):
        self.stop_event.set()
        self.running = False
        self._set_status("STOPPED", MUTED)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._log("Bridge stopped", "WARN")

    def test_osc(self):
        """Send a test OSC message to verify Companion connectivity."""
        for key, entry in self.fields.items():
            self.cfg[key] = entry.get().strip()
        host = self.cfg.get("companion_host", "127.0.0.1")
        port = self.cfg.get("companion_port", "12321")
        # Companion 3.x / 4.x: page 1, row 0, column 0
        address = "/location/1/0/0/press"
        try:
            host, port = send_osc(host, port, address)
            # Reflect cleaned host back into the field
            if "companion_host" in self.fields:
                self.fields["companion_host"].delete(0, "end")
                self.fields["companion_host"].insert(0, host)
                self.cfg["companion_host"] = host
                save_config(self.cfg)
            self._log(f"TEST OSC  {address}  →  {host}:{port}", "OK")
            self._log("If Companion did not fire: enable OSC Listener in Companion Settings (port 12321)", "INFO")
        except OSError as e:
            self._log(
                f"TEST OSC failed: {e} — Companion Host must be an IP/hostname (e.g. 127.0.0.1), not a URL",
                "ERR",
            )
        except Exception as e:
            self._log(f"TEST OSC failed: {e}", "ERR")

    def clear_log(self):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")

    def _set_status(self, text, color):
        self.status_canvas.itemconfig(self.status_dot, fill=color)
        self.status_label.config(text=text, fg=color)

    # ── Log polling ───────────────────────────────────────────────────────────

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._log(msg)
        except queue.Empty:
            pass
        self.after(150, self._poll_log)

    def _log(self, msg, level=None):
        # Auto-detect level from message if not provided
        if level is None:
            for tag in ("OSC", "ERR", "OK", "WARN"):
                if f"[{tag}]" in msg or msg.startswith(tag):
                    level = tag
                    break
            else:
                level = "INFO"

        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n", level)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def on_close(self):
        self.stop_event.set()
        self.destroy()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
